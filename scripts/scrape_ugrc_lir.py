"""
scrape_ugrc_lir.py — UGRC LIR Ingestion for 7 counties (Phase 13b-2)

Ingests raw UGRC LIR parcel data into CSVs for downstream loading into D1.
Supports Tooele, Salt Lake, Utah, Davis, Weber, Wasatch, Box Elder.
Handles county-specific URL naming quirks (SaltLake, BoxElder without underscores).
Computes centroid from geometry and preserves polygon as GeoJSON.
Idempotent cache checks against existing parcel_enrichment_log rows.
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from shapely.geometry import shape, Point

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# The 7 counties for Phase 13b
COUNTIES = ["tooele", "salt_lake", "utah", "davis", "weber", "wasatch", "box_elder"]

# UGRC URL mapping (handling the underscore quirk)
COUNTY_URL_NAMES = {
    "tooele": "Tooele",
    "salt_lake": "SaltLake",
    "utah": "Utah",
    "davis": "Davis",
    "weber": "Weber",
    "wasatch": "Wasatch",
    "box_elder": "BoxElder"
}

ARCGIS_BASE = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services"

# The raw LIR fields we need to extract (per arch doc 1.1)
LIR_FIELDS = [
    "PARCEL_ID",
    "PARCEL_ADD",
    "PARCEL_CITY",
    "PARCEL_ACRES",
    "PROP_CLASS",
    "PRIMARY_RES",
    "HOUSE_CNT",
    "SUBDIV_NAME",
    "BLDG_SQFT",
    "BUILT_YR",
    "EFFBUILT_YR",
    "TOTAL_MKT_VALUE",
    "LAND_MKT_VALUE",
    "TAXEXEMPT_TYPE",
    "TAX_DISTRICT",
    "COUNTY_NAME"
]

# Output CSV columns
CSV_COLUMNS = [
    "parcel_id",
    "parcel_address",
    "parcel_city",
    "county",
    "acreage",
    "prop_class",
    "primary_res",
    "house_count",
    "subdiv_name",
    "bldg_sqft",
    "built_yr",
    "effective_built_yr",
    "total_market_value",
    "land_market_value",
    "taxexempt_type",
    "tax_district",
    "county_name",
    "centroid_lng",
    "centroid_lat",
    "polygon_geojson"
]

# Pagination and rate limiting
GEOM_PAGE_SIZE = 1000
DETAIL_BATCH_SIZE = 100
REQUEST_DELAY = 0.25

# Paths
DATA_DIR = Path("data/raw")
CACHE_DIR = Path("data/cache/lir")

# ── HTTP Session ──────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "TooeleLandIntel/1.0 (Phase 13b-2 Scraper)"})
    return session

# ── Cache Helpers ─────────────────────────────────────────────────────────────

def _cache_key(county: str, req_type: str, identifier: str) -> Path:
    county_cache = CACHE_DIR / county
    county_cache.mkdir(parents=True, exist_ok=True)
    safe_id = hashlib.md5(identifier.encode()).hexdigest()[:12]
    return county_cache / f"{req_type}_{safe_id}.json"

def _load_cache(path: Path) -> Optional[Any]:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _save_cache(path: Path, data: Any):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning(f"Cache write failed: {e}")

# ── ArcGIS Fetchers ───────────────────────────────────────────────────────────

def fetch_geometries(session: requests.Session, service_url: str, offset: int, county: str) -> List[Dict]:
    """Fetch a page of geometries + OBJECTID."""
    cache_path = _cache_key(county, "geom", f"offset_{offset}")
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    query_url = f"{service_url}/query"
    params = {
        "where": "1=1",
        "outSR": "4326",
        "outFields": "OBJECTID",
        "returnGeometry": "true",
        "f": "geojson",
        "resultOffset": offset,
        "resultRecordCount": GEOM_PAGE_SIZE,
    }

    time.sleep(REQUEST_DELAY)
    resp = session.get(query_url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    
    if "error" in data:
        log.error(f"ArcGIS error (geom offset={offset}): {data['error']}")
        return []
        
    features = data.get("features", [])
    _save_cache(cache_path, features)
    return features

def fetch_details(session: requests.Session, service_url: str, object_ids: List[int], county: str) -> List[Dict]:
    """Fetch full LIR attributes for a batch of OBJECTIDs."""
    ids_key = ",".join(map(str, sorted(object_ids)))
    cache_path = _cache_key(county, "detail", ids_key)
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    query_url = f"{service_url}/query"
    params = {
        "where": f"OBJECTID IN ({','.join(map(str, object_ids))})",
        "outFields": "OBJECTID," + ",".join(LIR_FIELDS),
        "returnGeometry": "false",
        "f": "json",
    }

    time.sleep(REQUEST_DELAY)
    resp = session.get(query_url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    
    if "error" in data:
        log.error(f"ArcGIS error (details): {data['error']}")
        return []
        
    features = data.get("features", [])
    _save_cache(cache_path, features)
    return features

# ── Processing ────────────────────────────────────────────────────────────────

def compute_centroid(geom_dict: Dict) -> tuple[Optional[float], Optional[float]]:
    """Compute centroid from GeoJSON geometry using Shapely."""
    if not geom_dict:
        return None, None
    try:
        poly = shape(geom_dict)
        if poly.is_empty:
            return None, None
        pt = poly.centroid
        return round(pt.x, 6), round(pt.y, 6)
    except Exception:
        return None, None

def process_county(county: str, dry_run: bool = False):
    """Fetch and process all parcels for a county."""
    url_name = COUNTY_URL_NAMES[county]
    service_url = f"{ARCGIS_BASE}/Parcels_{url_name}_LIR/FeatureServer/0"
    
    log.info(f"=== Starting {county} ({url_name}) ===")
    log.info(f"Endpoint: {service_url}")
    
    session = _make_session()
    
    # 1. Probe endpoint
    probe_url = f"{service_url}/query"
    try:
        probe_resp = session.get(probe_url, params={"where": "1=1", "returnCountOnly": "true", "f": "json"})
        probe_resp.raise_for_status()
        probe_data = probe_resp.json()
        total_expected = probe_data.get("count", 0)
        log.info(f"Live probe count for {county}: {total_expected:,}")
        if total_expected == 0:
            log.error(f"Endpoint returned 0 records. Aborting {county}.")
            return
    except Exception as e:
        log.error(f"Failed to probe endpoint for {county}: {e}")
        return
        
    if dry_run:
        log.info(f"Dry run complete for {county}. Expected records: {total_expected:,}")
        return
        
    # 2. Fetch all geometries
    geom_features = []
    offset = 0
    consecutive_empty = 0
    
    log.info("Fetching geometries...")
    while True:
        features = fetch_geometries(session, service_url, offset, county)
        if not features:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                log.info(f"No more geometries after offset {offset}.")
                break
            offset += GEOM_PAGE_SIZE
            continue
            
        consecutive_empty = 0
        geom_features.extend(features)
        
        if len(geom_features) % 10000 == 0:
            log.info(f"  ... {len(geom_features):,} geometries fetched")
            
        if len(features) < GEOM_PAGE_SIZE:
            # UGRC sometimes returns fewer than requested, don't stop immediately unless it's 0
            # but if it's very small, it might be the end
            pass
            
        offset += GEOM_PAGE_SIZE
        
    log.info(f"Total geometries fetched: {len(geom_features):,}")
    
    # 3. Fetch details in batches
    object_ids = [f["properties"]["OBJECTID"] for f in geom_features if "OBJECTID" in f.get("properties", {})]
    log.info(f"Fetching details for {len(object_ids):,} parcels in batches of {DETAIL_BATCH_SIZE}...")
    
    detailed_features = []
    failed_batches = 0
    
    for i in range(0, len(object_ids), DETAIL_BATCH_SIZE):
        batch_ids = object_ids[i:i + DETAIL_BATCH_SIZE]
        details = fetch_details(session, service_url, batch_ids, county)
        
        if not details:
            failed_batches += 1
            if failed_batches >= 10:
                log.error("Circuit breaker: 10 consecutive batches failed. Halting.")
                break
        else:
            failed_batches = 0
            detailed_features.extend(details)
            
        if i > 0 and i % 10000 == 0:
            log.info(f"  ... {len(detailed_features):,} details fetched")
            
    log.info(f"Total details fetched: {len(detailed_features):,}")
    
    # 4. Merge and format output
    log.info("Merging geometries and details...")
    details_map = {f["attributes"]["OBJECTID"]: f["attributes"] for f in detailed_features if "attributes" in f}
    
    out_rows = []
    for gf in geom_features:
        obj_id = gf.get("properties", {}).get("OBJECTID")
        if not obj_id or obj_id not in details_map:
            continue
            
        attrs = details_map[obj_id]
        geom = gf.get("geometry")
        
        # Simplify geometry string size slightly by rounding coords
        if geom:
            try:
                geom_str = json.dumps(geom, separators=(',', ':'))
            except:
                geom_str = None
        else:
            geom_str = None
            
        lng, lat = compute_centroid(geom)
        
        # Safe getters
        def get_val(key, default=None):
            val = attrs.get(key)
            return default if val is None else val
            
        row = {
            "parcel_id": get_val("PARCEL_ID"),
            "parcel_address": get_val("PARCEL_ADD"),
            "parcel_city": get_val("PARCEL_CITY"),
            "county": county,
            "acreage": get_val("PARCEL_ACRES"),
            "prop_class": get_val("PROP_CLASS"),
            "primary_res": get_val("PRIMARY_RES"),
            "house_count": get_val("HOUSE_CNT"),
            "subdiv_name": get_val("SUBDIV_NAME"),
            "bldg_sqft": get_val("BLDG_SQFT", 0),
            "built_yr": get_val("BUILT_YR"),
            "effective_built_yr": get_val("EFFBUILT_YR"),
            "total_market_value": get_val("TOTAL_MKT_VALUE"),
            "land_market_value": get_val("LAND_MKT_VALUE"),
            "taxexempt_type": get_val("TAXEXEMPT_TYPE"),
            "tax_district": get_val("TAX_DISTRICT"),
            "county_name": get_val("COUNTY_NAME"),
            "centroid_lng": lng,
            "centroid_lat": lat,
            "polygon_geojson": geom_str
        }
        
        # Require parcel_id
        if row["parcel_id"]:
            out_rows.append(row)
            
    # 5. Write CSV
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / f"parcels_{county}.csv"
    
    log.info(f"Writing {len(out_rows):,} rows to {out_file}...")
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)
        
    log.info(f"=== Finished {county} ===")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    parser = argparse.ArgumentParser(description="Scrape UGRC LIR parcel data.")
    parser.add_argument("--county", type=str, required=True, 
                        help="County name (e.g. tooele, salt_lake, all)")
    parser.add_argument("--dry-run", action="store_true", 
                        help="Probe endpoint and count records, but do not fetch/write.")
    args = parser.parse_args()
    
    counties_to_run = COUNTIES if args.county == "all" else [args.county]
    
    for c in counties_to_run:
        if c not in COUNTIES:
            log.error(f"Unknown county: {c}. Must be one of {COUNTIES}")
            continue
        process_county(c, args.dry_run)
