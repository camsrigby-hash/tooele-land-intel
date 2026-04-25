"""fetch_stip.py — UDOT future road projects, Tooele-Valley-filtered.

Loosely ported from vendor/cm_re/stip/stip_fetcher.py — but the CM_RE host and
field schema were stale. This file uses the live UDOT Projects_as_Lines
FeatureServer (https://services.arcgis.com/pA2nEVnB6tquxgOW/) with its real
field names (pin, public_desc, pin_stat_nm, up_phase, cnty_name,
planned_construction_year, est_compl_dat, etc.).

Output: data/stip_projects.geojson — line features in WGS84.

Status filter: keep only future / in-progress projects per Phase 4 addendum
intent. UDOT's distinct status values for Tooele County are:
  Abandoned, Closed, Close Out, Physically Complete  → SKIP
  Active, Proposed, Scoping, Awarded, STIP, Under Construction → KEEP

Usage:
    python scripts/fetch_stip.py
"""

import json
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

EPM_URL = (
    "https://services.arcgis.com/pA2nEVnB6tquxgOW/ArcGIS/rest/services/"
    "Projects_as_Lines/FeatureServer/0"
)

# Tooele Valley bbox: roughly Stansbury/Lake Point (north) → Vernon (south),
# Oquirrhs (east) → Skull Valley (west). Wider than just Erda+Grantsville so
# Tooele City and Stansbury Park projects show up too.
TOOELE_BBOX = [-112.60, 40.35, -112.00, 40.95]

KEEP_STATUSES = {"Active", "Proposed", "Scoping", "Awarded", "STIP", "Under Construction"}

OUT_FIELDS = ",".join([
    "pin", "public_desc", "pin_desc", "concept_desc", "comments",
    "pin_stat_nm", "pin_status_phase_desc", "up_phase",
    "cnty_name", "route_desc", "proj_loc",
    "planned_construction_year", "projected_start_date", "est_compl_dat",
    "project_value", "stip_workshop_yr",
])

HEADERS = {"User-Agent": "TooeleLandIntel/1.0 (STIP fetcher)"}
OUT_PATH = Path("data/stip_projects.geojson")


def fetch_stip_projects() -> dict:
    log.info("Fetching UDOT projects in Tooele Valley bbox %s", TOOELE_BBOX)
    envelope = ",".join(str(x) for x in TOOELE_BBOX)

    status_list = ",".join(f"'{s}'" for s in sorted(KEEP_STATUSES))
    where = f"pin_stat_nm IN ({status_list})"

    all_features: list[dict] = []
    offset = 0
    page = 500

    while True:
        params = {
            "where": where,
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "outSR": "4326",
            "outFields": OUT_FIELDS,
            "returnGeometry": "true",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page,
        }
        try:
            resp = requests.get(f"{EPM_URL}/query", params=params, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("STIP fetch failed at offset %d: %s", offset, e)
            break

        if "error" in data:
            log.warning("ArcGIS error at offset %d: %s", offset, data["error"])
            break

        features = data.get("features", []) or []
        if not features:
            break
        all_features.extend(features)
        log.info("  fetched %d (running total %d)", len(features), len(all_features))
        if len(features) < page:
            break
        offset += page

    # Normalize properties to a stable, frontend-friendly shape.
    out_features: list[dict] = []
    for feat in all_features:
        props = feat.get("properties") or {}
        out_features.append({
            "type": "Feature",
            "geometry": feat.get("geometry"),
            "properties": {
                "pin":          props.get("pin"),
                "name":         props.get("public_desc") or props.get("pin_desc") or props.get("proj_loc") or "Unnamed project",
                "description":  props.get("concept_desc") or props.get("comments") or "",
                "status":       props.get("pin_stat_nm") or "",
                "phase":        props.get("pin_status_phase_desc") or "",
                "phase_num":    props.get("up_phase"),
                "county":       props.get("cnty_name") or "",
                "route":        props.get("route_desc") or "",
                "year_planned": props.get("planned_construction_year"),
                "year_complete":props.get("est_compl_dat"),
                "value":        props.get("project_value"),
                "stip_year":    props.get("stip_workshop_yr"),
            },
        })

    geojson = {"type": "FeatureCollection", "features": out_features}
    log.info("Wrote %d STIP/UDOT projects in Tooele Valley", len(out_features))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    log.info("Saved %s", OUT_PATH)
    return geojson


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    data = fetch_stip_projects()
    print(f"\nSTIP projects in Tooele Valley: {len(data['features'])}")
    for feat in data["features"][:8]:
        p = feat["properties"]
        print(f"  pin={p['pin']} | {p['status']:18s} | {p['route'][:25]:25s} | {(p['name'] or '')[:60]}")
