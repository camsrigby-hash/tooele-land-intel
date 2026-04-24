"""
parcel_fetcher.py — Resilient ArcGIS LIR parcel fetcher
Improvements over v1:
  - Local disk caching of raw API page responses (prevents re-fetching on crash)
  - Micro-batching: geometry pages of 50 records, detail batches of 50 OBJECTIDs
  - Correct LIR field names (confirmed via service metadata probe)
  - Graceful partial-result recovery from cache on restart
"""

import json
import hashlib
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from pathlib import Path

log = logging.getLogger(__name__)

# ── Directory layout ──────────────────────────────────────────────────────────
PARCEL_DIR  = Path("data/parcels")
CACHE_DIR   = Path("data/cache")          # Raw page-level API response cache
HEADERS     = {"User-Agent": "Mozilla/5.0 (compatible; RETool/1.0)"}

# ── ArcGIS LIR FeatureServer endpoints ───────────────────────────────────────
# Field schema confirmed via /FeatureServer/0?f=json metadata probe (29 fields)
COUNTY_SERVICES = {
    "davis": {
        "url":  "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0",
        "bbox": [-112.20, 40.82, -111.73, 41.17],
    },
    "weber": {
        "url":  "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0",
        "bbox": [-112.30, 41.07, -111.85, 41.45],
    },
}

# Confirmed available LIR fields (from metadata probe — no OWNER, ZONE, or IMPROV_MKT_VALUE)
LIR_FIELDS = [
    "PARCEL_ID",
    "PARCEL_ADD",
    "PARCEL_CITY",
    "PARCEL_ACRES",
    "PROP_CLASS",          # Property class (was PROPCLASS — incorrect)
    "PRIMARY_RES",         # Primary residence flag
    "HOUSE_CNT",           # Number of housing units
    "SUBDIV_NAME",         # Subdivision name
    "BLDG_SQFT",           # Building square footage
    "BUILT_YR",            # Year built
    "EFFBUILT_YR",         # Effective year built
    "TOTAL_MKT_VALUE",     # Total market value
    "LAND_MKT_VALUE",      # Land market value (improvement = total - land)
    "TAXEXEMPT_TYPE",      # Tax exempt type
    "TAX_DISTRICT",        # Tax district
    "COUNTY_NAME",         # County name
]

# ── Micro-batch sizes (resilience recommendation) ─────────────────────────────
GEOM_PAGE_SIZE    = 50    # Records per geometry page request
DETAIL_BATCH_SIZE = 50    # OBJECTIDs per detail request
REQUEST_DELAY     = 0.3   # Seconds between requests (rate limiting)


# ── HTTP session factory ──────────────────────────────────────────────────────
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
    session.mount("http://",  adapter)
    return session


# ── Cache helpers ─────────────────────────────────────────────────────────────
def _cache_key(county: str, request_type: str, identifier: str) -> Path:
    """Return a deterministic cache file path for a given request."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = hashlib.md5(identifier.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{county}_{request_type}_{safe_id}.json"


def _load_cache(path: Path):
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cache(path: Path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning(f"Cache write failed: {e}")


# ── Geometry fetch (one page) ─────────────────────────────────────────────────
def fetch_parcel_geometries(service_url: str, offset: int, bbox: list,
                             county: str = "unknown") -> list:
    """Fetch one micro-batch page of parcel geometries + OBJECTIDs."""
    cache_path = _cache_key(county, "geom", f"offset_{offset}")
    cached = _load_cache(cache_path)
    if cached is not None:
        log.debug(f"  [CACHE HIT] geom offset={offset}")
        return cached

    envelope = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    query_url = f"{service_url}/query"
    params = {
        "where":             "PARCEL_ACRES IS NOT NULL AND PARCEL_ACRES > 0",
        "geometry":          envelope,
        "geometryType":      "esriGeometryEnvelope",
        "spatialRel":        "esriSpatialRelIntersects",
        "inSR":              "4326",
        "outSR":             "4326",
        "outFields":         "OBJECTID",
        "returnGeometry":    True,
        "f":                 "geojson",
        "resultOffset":      offset,
        "resultRecordCount": GEOM_PAGE_SIZE,
    }

    session = _make_session()
    try:
        time.sleep(REQUEST_DELAY)
        resp = session.get(query_url, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.error(f"ArcGIS error (geom offset={offset}): {data['error']}")
            return []
        features = data.get("features", [])
        _save_cache(cache_path, features)
        return features
    except Exception as e:
        log.warning(f"Geometry fetch failed at offset {offset}: {e}")
        return []


# ── Detail fetch (one micro-batch) ────────────────────────────────────────────
def fetch_parcel_details(service_url: str, object_ids: list,
                          county: str = "unknown") -> list:
    """Fetch LIR attribute fields for a micro-batch of OBJECTIDs."""
    ids_key = ",".join(map(str, sorted(object_ids)))
    cache_path = _cache_key(county, "detail", ids_key)
    cached = _load_cache(cache_path)
    if cached is not None:
        log.debug(f"  [CACHE HIT] details batch size={len(object_ids)}")
        return cached

    query_url = f"{service_url}/query"
    params = {
        "where":          f"OBJECTID IN ({','.join(map(str, object_ids))})",
        "outFields":      "OBJECTID," + ",".join(LIR_FIELDS),
        "returnGeometry": False,
        "f":              "json",
    }

    session = _make_session()
    try:
        time.sleep(REQUEST_DELAY)
        resp = session.get(query_url, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.error(f"ArcGIS error (details): {data['error']}")
            return []
        features = data.get("features", [])
        _save_cache(cache_path, features)
        return features
    except Exception as e:
        log.warning(f"Details fetch failed for batch: {e}")
        return []


# ── County-level fetch ────────────────────────────────────────────────────────
def fetch_county_parcels(county: str, config: dict) -> dict:
    """
    Fetch all parcels for a county using micro-batched pagination.
    Geometry pages and detail batches are individually cached so a crash
    mid-run can resume from the last successful page.
    """
    log.info(f"Fetching {county.title()} County LIR parcels (micro-batch mode)...")
    geom_features = []
    offset = 0
    page = 0

    while True:
        log.info(f"  [{county.title()}] Geometry page {page} (offset {offset})...")
        features = fetch_parcel_geometries(config["url"], offset, config["bbox"], county)
        if not features:
            log.info(f"  [{county.title()}] No more geometry pages at offset {offset}.")
            break
        geom_features.extend(features)
        log.info(f"  [{county.title()}] {len(geom_features):,} geometries fetched so far.")

        if len(features) < GEOM_PAGE_SIZE:
            break  # Last page

        offset += GEOM_PAGE_SIZE
        page += 1

    if not geom_features:
        log.warning(f"  [{county.title()}] No geometries returned.")
        return {"type": "FeatureCollection", "features": []}

    log.info(f"  [{county.title()}] {len(geom_features):,} geometries total. Fetching LIR details...")

    # Fetch details in micro-batches
    object_ids = [f["properties"]["OBJECTID"] for f in geom_features]
    detailed_features = []
    for i in range(0, len(object_ids), DETAIL_BATCH_SIZE):
        batch_ids = object_ids[i : i + DETAIL_BATCH_SIZE]
        details = fetch_parcel_details(config["url"], batch_ids, county)
        detailed_features.extend(details)
        log.info(f"  [{county.title()}] Details: {len(detailed_features):,} / {len(object_ids):,}")

    # Merge attributes into geometry features
    details_map = {f["attributes"]["OBJECTID"]: f["attributes"] for f in detailed_features}
    merged = []
    for feature in geom_features:
        obj_id = feature["properties"]["OBJECTID"]
        if obj_id in details_map:
            feature["properties"].update(details_map[obj_id])
            merged.append(feature)

    log.info(f"  [{county.title()}] {len(merged):,} merged parcels.")
    return {"type": "FeatureCollection", "features": merged}


# ── Save to disk ──────────────────────────────────────────────────────────────
def save_parcels(county: str, geojson: dict) -> Path:
    PARCEL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PARCEL_DIR / f"{county}_parcels.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    size_mb = round(out_path.stat().st_size / 1024 / 1024, 2)
    log.info(f"  Saved: {out_path} ({size_mb} MB)")
    return out_path


# ── Public entry point ────────────────────────────────────────────────────────
def fetch_all_counties(force_refresh: bool = False) -> dict:
    """
    Fetch parcels for all configured counties.
    Uses county-level GeoJSON cache unless force_refresh=True.
    Page-level raw caches are always used regardless of force_refresh,
    so partial runs can resume without re-fetching completed pages.
    """
    results = {}
    for county, config in COUNTY_SERVICES.items():
        cache_path = PARCEL_DIR / f"{county}_parcels.geojson"

        if cache_path.exists() and not force_refresh:
            log.info(f"  [{county.title()}] Loading from county cache: {cache_path}")
            with open(cache_path, encoding="utf-8") as f:
                results[county] = json.load(f)
            log.info(f"  [{county.title()}] {len(results[county]['features']):,} parcels loaded from cache.")
            continue

        geojson = fetch_county_parcels(county, config)
        if geojson["features"]:
            save_parcels(county, geojson)
            results[county] = geojson
        else:
            log.warning(f"  [{county.title()}] No parcels returned — check API or bbox.")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    data = fetch_all_counties(force_refresh=True)
    total = sum(len(v["features"]) for v in data.values())
    print(f"\nTotal parcels fetched: {total:,}")
