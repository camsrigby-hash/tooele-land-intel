#!/usr/bin/env python3
"""
enrich_roads.py — Road adjacency enrichment for Tooele Valley parcels.

Reads data/gap_layer.geojson, fetches UGRC Utah Roads for the Tooele Valley
bbox, and computes per-parcel:
  - nearest_arterial_name, nearest_arterial_aadt, nearest_arterial_distance_mi
  - nearest_road_class
  - is_corner, corner_roads

Writes data/roads_enrichment.json keyed by APN so the Workers API can serve
this data from /api/parcel/:apn without live UGRC round-trips.

Ported from vendor/cm_re/parcel/road_adjacency.py — data-collection pattern only.
CRE scorer curves (score_aadt, score_arterial_access) excluded per
CM_RE_INTEGRATION.md §3 — TLI shows raw AADT and lets the human interpret it.

Usage:
    python scripts/enrich_roads.py
    python scripts/enrich_roads.py --force-refresh
"""

import argparse
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

ROADS_URL = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/UtahRoads/FeatureServer/0"
HEADERS   = {"User-Agent": "TooeleLandIntel/1.0"}

# Tooele Valley bbox (matches fetch_stip.py and build_gap_layer.py)
TOOELE_BBOX = (-112.60, 40.35, -112.00, 40.95)

# CARTOCODE 1-5 = interstates + major state highways (arterials for TLI purpose)
ARTERIAL_CARTOCODES  = ("1", "2", "3", "4", "5")
ALL_SCORED_CARTOCODES = ("1", "2", "3", "4", "5", "6", "7", "8")

CORNER_DETECTION_RADIUS_MI = 0.025  # ~40m — polygon edge to road

ROADS_CACHE_DIR = Path("data/cache/roads")
ROAD_FIELDS     = "OBJECTID,FULLNAME,NAME,CARTOCODE,DOT_FCLASS,DOT_AADT,SPEED_LMT"


# ── Geometry ───────────────────────────────────────────────────────────────────

def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_to_linestring_distance(plat: float, plon: float, coords: list) -> float:
    min_dist = float("inf")
    for i in range(len(coords) - 1):
        x1, y1 = coords[i][0],   coords[i][1]
        x2, y2 = coords[i+1][0], coords[i+1][1]
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            dist = haversine_mi(plat, plon, y1, x1)
        else:
            t = max(0, min(1, ((plon - x1)*dx + (plat - y1)*dy) / (dx*dx + dy*dy)))
            dist = haversine_mi(plat, plon, y1 + t*dy, x1 + t*dx)
        min_dist = min(min_dist, dist)
    return min_dist


def point_to_road_distance(plat: float, plon: float, road: dict) -> float:
    geom   = road.get("geometry", {})
    gtype  = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if gtype == "LineString":
        return point_to_linestring_distance(plat, plon, coords)
    if gtype == "MultiLineString":
        return min(point_to_linestring_distance(plat, plon, seg) for seg in coords)
    # Fallback centroid
    all_pts = [pt for seg in (coords if gtype == "MultiLineString" else [coords]) for pt in seg]
    if not all_pts:
        return float("inf")
    clat = sum(p[1] for p in all_pts) / len(all_pts)
    clon = sum(p[0] for p in all_pts) / len(all_pts)
    return haversine_mi(plat, plon, clat, clon)


def polygon_centroid(geometry: dict) -> tuple[float, float] | None:
    if not geometry or geometry.get("type") != "Polygon":
        return None
    ring = geometry.get("coordinates", [[]])[0]
    if not ring:
        return None
    lon = sum(p[0] for p in ring) / len(ring)
    lat = sum(p[1] for p in ring) / len(ring)
    return (lat, lon)


def polygon_edge_midpoints(geometry: dict) -> list[tuple[float, float]]:
    if not geometry or geometry.get("type") != "Polygon":
        return []
    ring = geometry.get("coordinates", [[]])[0]
    mids = []
    for i in range(len(ring) - 1):
        lat = (ring[i][1] + ring[i+1][1]) / 2
        lon = (ring[i][0] + ring[i+1][0]) / 2
        mids.append((lat, lon))
    return mids


# ── Road fetching ──────────────────────────────────────────────────────────────

def _cache_path(page: int) -> Path:
    ROADS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return ROADS_CACHE_DIR / f"tooele_roads_p{page:04d}.json"


def fetch_roads(force_refresh: bool = False) -> list:
    bbox = TOOELE_BBOX
    envelope = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    all_features = []
    page = 0
    page_size = 1000
    max_pages = 30

    log.info("Fetching roads for Tooele Valley (CARTOCODE 1-8)...")

    while page < max_pages:
        cache_file = _cache_path(page)
        if cache_file.exists() and not force_refresh:
            with open(cache_file) as f:
                feats = json.load(f)
            log.debug("  Page %d: %d roads from cache", page, len(feats))
            all_features.extend(feats)
            if len(feats) < page_size:
                break
            page += 1
            continue

        codes_sql = "(" + ",".join(f"'{c}'" for c in ALL_SCORED_CARTOCODES) + ")"
        params = {
            "where":             f"CARTOCODE IN {codes_sql}",
            "geometry":          envelope,
            "geometryType":      "esriGeometryEnvelope",
            "spatialRel":        "esriSpatialRelIntersects",
            "inSR":              "4326",
            "outSR":             "4326",
            "outFields":         ROAD_FIELDS,
            "returnGeometry":    True,
            "resultOffset":      page * page_size,
            "resultRecordCount": page_size,
            "f":                 "geojson",
        }
        try:
            r = requests.get(f"{ROADS_URL}/query", params=params, headers=HEADERS, timeout=60)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                log.error("Roads API error: %s", data["error"])
                break
            feats = data.get("features", [])
            log.info("  Page %d: %d roads", page, len(feats))
            with open(cache_file, "w") as f:
                json.dump(feats, f)
            all_features.extend(feats)
            if len(feats) < page_size:
                break
            page += 1
            time.sleep(0.3)
        except requests.RequestException as e:
            log.error("Roads fetch error (page %d): %s", page, e)
            break

    log.info("Total: %d road segments", len(all_features))
    return all_features


# ── Spatial index ──────────────────────────────────────────────────────────────

class RoadSpatialIndex:
    def __init__(self, features: list, cell_size: float = 0.01):
        self.cell_size = cell_size
        self.grid: dict[tuple, list] = {}
        for feat in features:
            geom   = feat.get("geometry", {})
            gtype  = geom.get("type", "")
            coords = geom.get("coordinates", [])
            pts = coords if gtype == "LineString" else [pt for seg in coords for pt in seg]
            seen: set = set()
            for pt in pts:
                cell = (int(pt[1] / cell_size), int(pt[0] / cell_size))
                if cell not in seen:
                    seen.add(cell)
                    self.grid.setdefault(cell, []).append(feat)

    def query_nearby(self, lat: float, lon: float, radius_deg: float) -> list:
        r = int(radius_deg / self.cell_size) + 1
        br, bc = int(lat / self.cell_size), int(lon / self.cell_size)
        seen_ids: set = set()
        results = []
        for dr in range(-r, r + 1):
            for dc in range(-r, r + 1):
                for feat in self.grid.get((br + dr, bc + dc), []):
                    oid = feat.get("properties", {}).get("OBJECTID")
                    if oid not in seen_ids:
                        seen_ids.add(oid)
                        results.append(feat)
        return results


# ── Per-parcel computation ─────────────────────────────────────────────────────

def analyze_parcel(lat: float, lon: float, geometry: dict, road_index: RoadSpatialIndex) -> dict:
    candidates = road_index.query_nearby(lat, lon, 0.02)  # ~1.4 mi search radius

    if not candidates:
        return {
            "nearest_arterial_name":        None,
            "nearest_arterial_aadt":        None,
            "nearest_arterial_distance_mi": None,
            "nearest_road_class":           None,
            "is_corner":                    False,
            "corner_roads":                 [],
        }

    road_dists = []
    for feat in candidates:
        props = feat.get("properties", {})
        dist  = point_to_road_distance(lat, lon, feat)
        road_dists.append({
            "dist":      dist,
            "name":      (props.get("FULLNAME") or props.get("NAME") or "").strip(),
            "cartocode": str(props.get("CARTOCODE") or "11"),
            "fclass":    props.get("DOT_FCLASS") or "",
            "aadt":      int(props.get("DOT_AADT") or 0),
        })
    road_dists.sort(key=lambda x: x["dist"])

    # Nearest arterial
    arterials = [r for r in road_dists if r["cartocode"] in ARTERIAL_CARTOCODES]
    if arterials:
        best = arterials[0]
        nearest_name  = best["name"] or None
        nearest_aadt  = best["aadt"] or None
        nearest_dist  = round(best["dist"], 3)
        nearest_class = best["fclass"] or f"CARTOCODE-{best['cartocode']}"
    else:
        nearest_name  = None
        nearest_aadt  = None
        nearest_dist  = None
        nearest_class = None

    # Corner detection — edge-midpoint proximity to distinct named roads
    edge_mids = polygon_edge_midpoints(geometry)
    corner_names: set = set()
    if edge_mids:
        for road in candidates:
            props     = road.get("properties", {})
            name      = (props.get("FULLNAME") or props.get("NAME") or "").strip()
            cartocode = str(props.get("CARTOCODE") or "11")
            if not name or cartocode not in ALL_SCORED_CARTOCODES:
                continue
            for mid_lat, mid_lon in edge_mids:
                if point_to_road_distance(mid_lat, mid_lon, road) <= CORNER_DETECTION_RADIUS_MI:
                    corner_names.add(name)
                    break
    is_corner    = len(corner_names) >= 2
    corner_roads = sorted(corner_names)[:4]

    return {
        "nearest_arterial_name":        nearest_name,
        "nearest_arterial_aadt":        nearest_aadt,
        "nearest_arterial_distance_mi": nearest_dist,
        "nearest_road_class":           nearest_class,
        "is_corner":                    is_corner,
        "corner_roads":                 corner_roads,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Enrich gap_layer parcels with road adjacency data.")
    parser.add_argument("--force-refresh", action="store_true", help="Re-fetch road segments (ignores cache)")
    args = parser.parse_args()

    repo_root  = Path(__file__).parent.parent
    gap_path   = repo_root / "data" / "gap_layer.geojson"
    out_path   = repo_root / "data" / "roads_enrichment.json"

    if not gap_path.exists():
        log.error("gap_layer.geojson not found — run build_gap_layer.py first")
        raise SystemExit(1)

    with open(gap_path, encoding="utf-8") as f:
        fc = json.load(f)

    features = fc.get("features", [])
    developable = [f for f in features if f.get("properties", {}).get("developable")]
    log.info("Loaded %d features (%d developable) from gap_layer.geojson", len(features), len(developable))

    road_features = fetch_roads(force_refresh=args.force_refresh)
    if not road_features:
        log.error("No road features fetched — check UGRC connectivity")
        raise SystemExit(1)

    road_index = RoadSpatialIndex(road_features)
    log.info("Road index built: %d grid cells", len(road_index.grid))

    result: dict = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "parcel_count":   len(developable),
        "road_segments":  len(road_features),
    }

    processed = 0
    for feat in developable:
        props    = feat.get("properties", {})
        apn      = props.get("apn")
        geometry = feat.get("geometry", {})
        if not apn:
            continue

        centroid = polygon_centroid(geometry)
        if not centroid:
            result[apn] = {
                "nearest_arterial_name": None,
                "nearest_arterial_aadt": None,
                "nearest_arterial_distance_mi": None,
                "nearest_road_class": None,
                "is_corner": False,
                "corner_roads": [],
            }
            continue

        lat, lon = centroid
        result[apn] = analyze_parcel(lat, lon, geometry, road_index)
        processed += 1

        if processed % 500 == 0:
            log.info("  Processed %d / %d parcels", processed, len(developable))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, separators=(",", ":"))

    log.info("Wrote %s (%d parcel entries)", out_path, processed)


if __name__ == "__main__":
    main()
