"""build_gap_layer.py — Zoning vs General Plan gap layer for Erda + Grantsville.

For every parcel inside each MVP city's bbox:
  1. Pull parcel polygon from the Tooele County parcel layer
  2. Spatial-join against that city's zoning layer (sublayer 1 = Erda, 7 = Grantsville)
  3. Spatial-join against the 2022 Tooele County GP layer (partial coverage)
  4. Map zoning code → intensity 1-10, GP designation → intensity 1-10
  5. gap_score = max(0, gp_intensity - zoning_intensity)

Output: data/gap_layer.geojson — FeatureCollection of parcel polygons in WGS84
with properties { apn, zoning, generalPlan, gap_score, jurisdiction, acres,
zoning_intensity, gp_intensity }.

Honest caveats:
  - GP coverage is patchy (Tooele Co 2022 GP only). Parcels with no GP hit
    get gap_score = null (rendered transparent).
  - Unknown zoning codes also yield gap_score = null.

Usage:
    python scripts/build_gap_layer.py
    python scripts/build_gap_layer.py --city erda    # one city only
    python scripts/build_gap_layer.py --max 0        # no parcel cap (default 8000/city)
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Iterable

import requests
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

log = logging.getLogger(__name__)

ARCGIS_BASE = "https://tcgisws.tooeleco.gov/server/rest/services"
PARCELS_URL = f"{ARCGIS_BASE}/Parcels/MapServer/0"
GP_URL      = f"{ARCGIS_BASE}/GeneralPlan_2022_LandUseCA/MapServer/0"

HEADERS = {"User-Agent": "TooeleLandIntel/1.0 (gap layer builder)"}
REQUEST_DELAY = 0.25
TIMEOUT = 60
PAGE_SIZE = 1000

OUT_PATH = Path("data/gap_layer.geojson")


# ── City configs ────────────────────────────────────────────────────────────
# bbox is [west, south, east, north] in WGS84.
# zoning_url is the city's zoning sublayer; zone_field is the column.

# Zoning sublayers we union into one spatial index. Field names vary —
# Erda + County use "Zone"; Grantsville uses "Zoning".
ZONING_SUBLAYERS: list[tuple[str, int, str]] = [
    ("Erda",                 1, "Zone"),
    ("Tooele County (uninc)", 4, "Zone"),
    ("Grantsville",          7, "Zoning"),
]

CITIES: dict[str, dict] = {
    "erda": {
        "label": "Erda",
        # Erda municipal area + neighboring unincorporated parcels that share
        # the same Tooele Co GP coverage.
        "bbox":  [-112.42, 40.57, -112.30, 40.66],
    },
    "grantsville": {
        "label": "Grantsville",
        "bbox":  [-112.55, 40.55, -112.42, 40.66],
    },
}


# ── Intensity tables (hand-curated from observed codes) ─────────────────────
# Higher = denser / more developed-friendly. Unknown → None → gap_score=null.

ZONING_INTENSITY: dict[str, int] = {
    # Agricultural
    "A-20": 1, "A-40": 1, "A-10": 2, "AG": 1, "A-1": 2,
    # Rural residential (large lots)
    "RR-10": 2, "RR-5": 3, "RR-2.5": 3, "RR-1": 4, "RR-2": 3,
    # Single-family residential (lot size shrinks → intensity rises)
    "R-1-21": 4, "R-1-12": 5, "R-1-10": 5, "R-1-8": 6,
    "R-1": 5, "R-2": 6, "R-3": 7,
    # Multi-family / mixed-density residential
    "RM-7": 7, "RM-15": 7, "MD": 6, "MU": 8, "MU-40": 8,
    # Commercial
    "CN": 7, "CD": 8, "CG": 8, "CH": 8, "CS": 7, "CM": 8, "C-T": 7,
    # Industrial / manufacturing
    "MG": 7, "MG-EX": 7, "M": 7, "EM": 6,
    # Public / civic / planned
    "P-2": 3, "P-C": 5, "PUD": 7,
}

GP_INTENSITY: dict[str, int] = {
    "AG": 1,        # Agricultural
    "OS": 1,        # Open Space
    "LIR": 4,       # Low-Intensity Residential
    "MIR": 6,       # Medium-Intensity Residential
    "HIR": 8,       # High-Intensity Residential
    "CM": 8,        # Commercial
    "CS": 7,        # Commercial Services
    "M":  7,        # Manufacturing
    "EM": 6,        # Extractive / Mining
    "MU": 8,        # Mixed Use
}


# ── ArcGIS helpers ──────────────────────────────────────────────────────────

def _fetch_geojson(url: str, where: str = "1=1", bbox: list[float] | None = None,
                   out_fields: str = "*", offset: int = 0, page: int = PAGE_SIZE) -> dict:
    params: dict = {
        "where": where,
        "outFields": out_fields,
        "outSR": "4326",
        "returnGeometry": "true",
        "f": "geojson",
        "resultOffset": offset,
        "resultRecordCount": page,
    }
    if bbox is not None:
        params.update({
            "geometry":      ",".join(str(x) for x in bbox),
            "geometryType":  "esriGeometryEnvelope",
            "spatialRel":    "esriSpatialRelIntersects",
            "inSR":          "4326",
        })
    resp = requests.get(f"{url}/query", params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return data


def fetch_paged(url: str, bbox: list[float] | None = None, where: str = "1=1",
                out_fields: str = "*", max_features: int = 0) -> list[dict]:
    features: list[dict] = []
    offset = 0
    while True:
        data = _fetch_geojson(url, where=where, bbox=bbox, out_fields=out_fields, offset=offset)
        page_features = data.get("features", []) or []
        if not page_features:
            break
        features.extend(page_features)
        log.info("    page offset=%d → %d (running %d)", offset, len(page_features), len(features))
        if len(page_features) < PAGE_SIZE:
            break
        if max_features and len(features) >= max_features:
            log.info("    cap %d reached, stopping", max_features)
            features = features[:max_features]
            break
        offset += PAGE_SIZE
        time.sleep(REQUEST_DELAY)
    return features


# ── Spatial index helpers ──────────────────────────────────────────────────

def _build_index(features: Iterable[dict]):
    geoms, payload = [], []
    for f in features:
        g = f.get("geometry")
        if not g:
            continue
        try:
            poly = shape(g)
        except Exception:
            continue
        if poly.is_empty:
            continue
        geoms.append(poly)
        payload.append((poly, f.get("properties") or {}))
    tree = STRtree(geoms) if geoms else None
    return tree, payload


def _lookup(tree: STRtree | None, payload: list[tuple], pt: Point) -> dict | None:
    if tree is None:
        return None
    candidates = tree.query(pt)
    for idx in candidates:
        poly, props = payload[idx]
        if poly.contains(pt) or poly.intersects(pt):
            return props
    return None


# ── Per-city build ─────────────────────────────────────────────────────────

def fetch_all_zoning() -> list[dict]:
    """Pull every zoning sublayer once and normalize its zone code field."""
    out: list[dict] = []
    for label, layer_id, field in ZONING_SUBLAYERS:
        url = f"{ARCGIS_BASE}/Zoning/MapServer/{layer_id}"
        log.info("  zoning sublayer %d (%s, field=%s)", layer_id, label, field)
        feats = fetch_paged(url, out_fields=field)
        for f in feats:
            props = f.get("properties") or {}
            f["properties"] = {
                "Zone":       (props.get(field) or "").strip() or None,
                "ZoningJurisdiction": label,
            }
            out.append(f)
        log.info("    → %d", len(feats))
    log.info("  zoning union: %d polygons", len(out))
    return out


def build_city(city_key: str, max_parcels: int,
               zone_tree, zone_payload) -> list[dict]:
    cfg = CITIES[city_key]
    label = cfg["label"]
    bbox = cfg["bbox"]
    log.info("=== Building gap layer for %s (bbox=%s) ===", label, bbox)

    log.info("  Fetching GP polygons in bbox...")
    gp_features = fetch_paged(GP_URL, bbox=bbox, out_fields="Landuse_Ca,Name")
    log.info("  → %d GP polygons", len(gp_features))

    log.info("  Fetching parcel polygons in bbox (this is the slow part)...")
    parcel_features = fetch_paged(
        PARCELS_URL, bbox=bbox,
        out_fields="Parcel_ID,SitusAddress,TotalAcres,PrimaryOwnerName,PropertyCodes",
        max_features=max_parcels,
    )
    log.info("  → %d parcels", len(parcel_features))

    gp_tree, gp_payload = _build_index(gp_features)

    out: list[dict] = []
    n_zoned = n_gp = n_scored = 0
    for pf in parcel_features:
        geom = pf.get("geometry")
        props = pf.get("properties") or {}
        if not geom:
            continue
        try:
            poly = shape(geom)
        except Exception:
            continue
        if poly.is_empty:
            continue
        try:
            centroid = poly.representative_point()
        except Exception:
            centroid = poly.centroid

        zone_props = _lookup(zone_tree, zone_payload, centroid)
        zone_code = (zone_props or {}).get("Zone")
        zone_jurisdiction = (zone_props or {}).get("ZoningJurisdiction")

        gp_props = _lookup(gp_tree, gp_payload, centroid)
        gp_code = (gp_props or {}).get("Landuse_Ca")
        gp_code = (gp_code or "").strip() or None

        z_int = ZONING_INTENSITY.get(zone_code) if zone_code else None
        g_int = GP_INTENSITY.get(gp_code) if gp_code else None

        if zone_code:
            n_zoned += 1
        if gp_code:
            n_gp += 1
        gap_score = None
        if z_int is not None and g_int is not None:
            gap_score = max(0, g_int - z_int)
            n_scored += 1

        apn = props.get("Parcel_ID")
        out.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "apn":              apn,
                "jurisdiction":     label,
                "zoning":           zone_code,
                "zoning_jurisdiction": zone_jurisdiction,
                "zoning_intensity": z_int,
                "generalPlan":      gp_code,
                "gp_intensity":     g_int,
                "gap_score":        gap_score,
                "acres":            props.get("TotalAcres"),
                "owner":            props.get("PrimaryOwnerName"),
                "address":          props.get("SitusAddress"),
            },
        })

    log.info(
        "  %s totals: parcels=%d zoned=%d gp_hit=%d scored=%d (%.0f%% have gap_score)",
        label, len(out), n_zoned, n_gp, n_scored,
        100.0 * n_scored / max(1, len(out)),
    )
    return out


def main(only_city: str | None, max_parcels: int) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cities = [only_city] if only_city else list(CITIES.keys())

    log.info("Pre-loading zoning sublayers (Erda + Tooele Co + Grantsville)...")
    zoning_features = fetch_all_zoning()
    zone_tree, zone_payload = _build_index(zoning_features)

    all_features: list[dict] = []
    for c in cities:
        if c not in CITIES:
            raise SystemExit(f"unknown city: {c}")
        all_features.extend(build_city(c, max_parcels, zone_tree, zone_payload))

    # Round coords to 6 decimals (~10cm) to slim the geojson by ~40%.
    def _round(c):
        if isinstance(c, list):
            return [_round(x) for x in c]
        if isinstance(c, (int, float)):
            return round(c, 6)
        return c
    for f in all_features:
        if f.get("geometry") and "coordinates" in f["geometry"]:
            f["geometry"]["coordinates"] = _round(f["geometry"]["coordinates"])

    geojson = {"type": "FeatureCollection", "features": all_features}
    OUT_PATH.write_text(json.dumps(geojson, separators=(",", ":")), encoding="utf-8")
    log.info("Wrote %s — %d features (~%.1f MB)",
             OUT_PATH, len(all_features), OUT_PATH.stat().st_size / 1024 / 1024)

    high_gap = sum(1 for f in all_features if (f["properties"].get("gap_score") or 0) >= 4)
    log.info("High-gap parcels (gap_score>=4): %d", high_gap)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", choices=list(CITIES.keys()), default=None,
                    help="Build only this city (default: all MVP cities)")
    ap.add_argument("--max", type=int, default=8000,
                    help="Per-city parcel cap (0 = no cap; default 8000)")
    args = ap.parse_args()
    main(args.city, args.max)
