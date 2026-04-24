"""
stip_fetcher.py - Fetches UDOT future road/intersection projects
from the EPM All Projects as Lines dataset (refreshed daily).
Filters to Davis and Weber county area and future/active projects.
"""

import json
import logging
import requests
from pathlib import Path

log = logging.getLogger(__name__)

STIP_DIR = Path("data/stip")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RETool/1.0)"}

# UDOT EPM - All Projects as Lines (includes STIP, future planned projects)
EPM_URL = "https://services1.arcgis.com/vdNDkVykv9vEWFX4/ArcGIS/rest/services/EPM_Projects_Lines/FeatureServer/0"

# Bounding box covering Davis + Weber counties
STUDY_BBOX = [-112.30, 40.82, -111.73, 41.45]

# Project types we care about (road construction, intersection, widening)
RELEVANT_KEYWORDS = [
    "intersection", "interchange", "widening", "new road", "new highway",
    "corridor", "arterial", "construction", "reconstruction", "extension",
    "overpass", "underpass", "grade separation", "access", "frontage"
]

# Status codes indicating future/active projects
FUTURE_STATUSES = [
    "Active", "Programmed", "Planned", "Scoping", "Design",
    "Environmental", "Right of Way", "Construction", "Funded"
]


def fetch_stip_projects(force_refresh: bool = False) -> dict:
    """Fetch UDOT future road projects for the study area."""
    STIP_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = STIP_DIR / "udot_projects.geojson"

    if cache_path.exists() and not force_refresh:
        log.info(f"Loading cached STIP data: {cache_path}")
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"  {len(data['features'])} UDOT projects loaded from cache")
        return data

    log.info("Fetching UDOT EPM project data...")
    envelope = f"{STUDY_BBOX[0]},{STUDY_BBOX[1]},{STUDY_BBOX[2]},{STUDY_BBOX[3]}"

    all_features = []
    offset = 0
    max_records = 1000

    while True:
        query_url = f"{EPM_URL}/query"
        params = {
            "where": "1=1",
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "outSR": "4326",
            "outFields": "*",
            "returnGeometry": True,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": max_records,
        }

        try:
            resp = requests.get(query_url, params=params, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])
            if not features:
                break
            all_features.extend(features)
            log.info(f"  Fetched {len(all_features)} projects...")
            if len(features) < max_records:
                break
            offset += max_records
        except Exception as e:
            log.warning(f"STIP fetch failed at offset {offset}: {e}")
            break

    # Filter to relevant road projects
    filtered = []
    for feat in all_features:
        props = feat.get("properties") or feat.get("attributes", {})
        name = str(props.get("PROJECTNAME", "") or props.get("PROJECT_NAME", "") or "").lower()
        desc = str(props.get("DESCRIPTION", "") or props.get("PROJDESC", "") or "").lower()
        status = str(props.get("STATUS", "") or props.get("PROJECTSTATUS", "") or "")
        phase = str(props.get("PHASE", "") or "").lower()

        # Check if relevant keyword appears in name or description
        is_road_project = any(kw in name or kw in desc for kw in RELEVANT_KEYWORDS)

        # Check if it's a future/active project
        is_future = any(s.lower() in status.lower() for s in FUTURE_STATUSES)

        # Also include construction phase projects
        is_construction = "construction" in phase or "design" in phase

        if is_road_project or is_future or is_construction:
            # Normalize properties
            feat["properties"] = {
                "project_name": props.get("PROJECTNAME") or props.get("PROJECT_NAME", "Unknown"),
                "description":  props.get("DESCRIPTION") or props.get("PROJDESC", ""),
                "status":        status,
                "phase":         props.get("PHASE", ""),
                "pin":           props.get("PIN") or props.get("PROJECTPIN", ""),
                "funding":       props.get("TOTALFUNDING") or props.get("FUNDING", 0),
                "year_start":    props.get("CONSTRUCTIONYEAR") or props.get("STARTYEAR", ""),
                "year_end":      props.get("COMPLETIONYEAR") or props.get("ENDYEAR", ""),
                "county":        props.get("COUNTY", ""),
                "route":         props.get("ROUTE") or props.get("ROUTENAME", ""),
            }
            filtered.append(feat)

    geojson = {"type": "FeatureCollection", "features": filtered}
    log.info(f"  {len(filtered)} relevant road projects found in Davis/Weber area")

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    log.info(f"  Saved: {cache_path}")

    return geojson


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    data = fetch_stip_projects(force_refresh=True)
    print(f"UDOT projects fetched: {len(data['features'])}")
    for feat in data["features"][:5]:
        p = feat["properties"]
        print(f"  {p['project_name']} | {p['status']} | {p['year_start']}")
