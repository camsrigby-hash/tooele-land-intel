"""
road_adjacency.py — Road Adjacency Scoring Module (v1)

Fetches UGRC Utah Roads centerlines and computes:
  1. Nearest arterial road distance (for arterial access score)
  2. Corner lot detection (parcel adjacent to 2+ different named roads)
  3. AADT-based traffic score (real DOT_AADT values from UGRC)

UGRC Roads Service:
  https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/UtahRoads/FeatureServer/0

CARTOCODE domain (key values):
  1  = Interstates
  2  = US Highways, Separated
  3  = US Highways, Unseparated
  4  = Major State Highways, Separated
  5  = Major State Highways, Unseparated
  6  = Other State Highways
  7  = Ramps, Collectors
  8  = Major Local Roads, Paved
  11 = Other Local / Neighborhood Roads

DOT_FCLASS domain:
  Interstate, Other Freeway, Principal Arterial, Minor Arterial,
  Major Collector, Minor Collector, Local
"""

import json
import logging
import math
import hashlib
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

ROADS_URL = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/UtahRoads/FeatureServer/0"
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; RETool/1.0)"}

# Road class thresholds
# CARTOCODE 1-5: highways and major state roads (high AADT)
# CARTOCODE 6-8: other state + major local roads (moderate AADT)
ARTERIAL_CARTOCODES  = ("1", "2", "3", "4", "5")
COLLECTOR_CARTOCODES = ("6", "7", "8")
ALL_SCORED_CARTOCODES = ARTERIAL_CARTOCODES + COLLECTOR_CARTOCODES

# Proximity thresholds (miles)
CORNER_DETECTION_RADIUS_MI  = 0.10   # ~160m — centroid to road (legacy fallback)
EDGE_CORNER_RADIUS_MI       = 0.025  # ~40m  — polygon edge to road (preferred)
ARTERIAL_INFLUENCE_RADIUS_MI = 0.25  # ~400m — road within this distance gets AADT credit

# Cache directory
ROADS_CACHE_DIR = Path("data/cache/roads")

# County bounding boxes (WGS84: minLon, minLat, maxLon, maxLat)
COUNTY_BBOXES = {
    "davis": (-112.15, 40.77, -111.73, 41.10),
    "weber": (-112.35, 41.05, -111.85, 41.45),
}

# Road fields to fetch
ROAD_FIELDS = "OBJECTID,FULLNAME,NAME,CARTOCODE,DOT_FCLASS,DOT_AADT,SPEED_LMT,DOT_HWYNAM,ONEWAY"


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in miles between two lat/lon points."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_to_linestring_distance(plat: float, plon: float, coords: list) -> float:
    """
    Approximate minimum distance (miles) from a point to a LineString.
    Uses segment-wise perpendicular projection.
    """
    min_dist = float("inf")
    for i in range(len(coords) - 1):
        x1, y1 = coords[i][0],   coords[i][1]
        x2, y2 = coords[i+1][0], coords[i+1][1]

        # Project point onto segment in lat/lon space
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            dist = haversine(plat, plon, y1, x1)
        else:
            t = max(0, min(1, ((plon - x1)*dx + (plat - y1)*dy) / (dx*dx + dy*dy)))
            proj_lon = x1 + t * dx
            proj_lat = y1 + t * dy
            dist = haversine(plat, plon, proj_lat, proj_lon)
        min_dist = min(min_dist, dist)
    return min_dist


def point_to_road_distance(plat: float, plon: float, road_feature: dict) -> float:
    """Return minimum distance (miles) from a point to a road feature's geometry."""
    geom = road_feature.get("geometry", {})
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    if gtype == "LineString":
        return point_to_linestring_distance(plat, plon, coords)
    elif gtype == "MultiLineString":
        return min(point_to_linestring_distance(plat, plon, seg) for seg in coords)
    else:
        # Fallback: centroid distance
        all_pts = coords if gtype == "LineString" else [pt for seg in coords for pt in seg]
        if not all_pts:
            return float("inf")
        clat = sum(p[1] for p in all_pts) / len(all_pts)
        clon = sum(p[0] for p in all_pts) / len(all_pts)
        return haversine(plat, plon, clat, clon)


# ── Road data fetching ────────────────────────────────────────────────────────

def _cache_path(county: str, page: int) -> Path:
    ROADS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return ROADS_CACHE_DIR / f"{county}_roads_p{page:04d}.json"


def fetch_roads_for_county(county: str, force_refresh: bool = False) -> list:
    """
    Fetch all arterial + collector roads for a county from UGRC.
    Returns list of GeoJSON features.
    Caches pages to disk for crash recovery.
    """
    bbox = COUNTY_BBOXES.get(county)
    if not bbox:
        log.error(f"Unknown county: {county}")
        return []

    envelope = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    all_features = []
    page = 0
    page_size = 1000
    max_pages = 50  # safety cap

    log.info(f"  Fetching roads for {county.title()} County (CARTOCODE 1-8)...")

    while page < max_pages:
        cache_file = _cache_path(county, page)
        if cache_file.exists() and not force_refresh:
            with open(cache_file) as f:
                feats = json.load(f)
            log.debug(f"    Page {page}: loaded {len(feats)} roads from cache")
            all_features.extend(feats)
            if len(feats) < page_size:
                break
            page += 1
            continue

        # Build quoted string list for SQL IN clause: ('1','2','3',...)
        codes_sql = "(" + ",".join(f"'{c}'" for c in ALL_SCORED_CARTOCODES) + ")"
        params = {
            "where":          f"CARTOCODE IN {codes_sql}",
            "geometry":       envelope,
            "geometryType":   "esriGeometryEnvelope",
            "spatialRel":     "esriSpatialRelIntersects",
            "inSR":           "4326",
            "outSR":          "4326",
            "outFields":      ROAD_FIELDS,
            "returnGeometry": True,
            "resultOffset":   page * page_size,
            "resultRecordCount": page_size,
            "f":              "geojson",
        }

        try:
            r = requests.get(f"{ROADS_URL}/query", params=params, headers=HEADERS, timeout=60)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                log.error(f"    Roads API error: {data['error']}")
                break
            feats = data.get("features", [])
            log.info(f"    Page {page}: {len(feats)} roads fetched")

            with open(cache_file, "w") as f:
                json.dump(feats, f)

            all_features.extend(feats)
            if len(feats) < page_size:
                break
            page += 1
            time.sleep(0.3)

        except requests.RequestException as e:
            log.error(f"    Roads fetch error (page {page}): {e}")
            time.sleep(2)
            break

    log.info(f"  {county.title()} County: {len(all_features):,} road segments loaded")
    return all_features


def load_all_roads(counties: list = None, force_refresh: bool = False) -> dict:
    """
    Load road data for all specified counties.
    Returns dict: {county: [road_features]}
    """
    if counties is None:
        counties = list(COUNTY_BBOXES.keys())

    roads_by_county = {}
    for county in counties:
        roads_by_county[county] = fetch_roads_for_county(county, force_refresh=force_refresh)

    total = sum(len(v) for v in roads_by_county.values())
    log.info(f"  Total roads loaded: {total:,} segments across {len(counties)} counties")
    return roads_by_county


# ── Spatial index (simple grid) ───────────────────────────────────────────────

class RoadSpatialIndex:
    """
    A lightweight grid-based spatial index for fast road proximity queries.
    Divides the bounding box into a grid and bins road segments by cell.
    """

    def __init__(self, road_features: list, cell_size_deg: float = 0.01):
        """
        Build index from a list of GeoJSON road features.
        cell_size_deg ≈ 0.01 degrees ≈ ~0.7 miles — good for sub-mile queries.
        """
        self.cell_size = cell_size_deg
        self.grid: dict[tuple, list] = {}
        self._index(road_features)

    def _cell(self, lat: float, lon: float) -> tuple:
        return (int(lat / self.cell_size), int(lon / self.cell_size))

    def _index(self, features: list):
        for feat in features:
            geom = feat.get("geometry", {})
            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])

            pts = []
            if gtype == "LineString":
                pts = coords
            elif gtype == "MultiLineString":
                pts = [pt for seg in coords for pt in seg]

            seen_cells = set()
            for pt in pts:
                cell = self._cell(pt[1], pt[0])
                if cell not in seen_cells:
                    seen_cells.add(cell)
                    if cell not in self.grid:
                        self.grid[cell] = []
                    self.grid[cell].append(feat)

    def query_nearby(self, lat: float, lon: float, radius_deg: float) -> list:
        """Return all road features whose grid cells overlap the query radius."""
        cells_radius = int(radius_deg / self.cell_size) + 1
        base_row = int(lat / self.cell_size)
        base_col = int(lon / self.cell_size)
        candidates = []
        seen_ids = set()

        for dr in range(-cells_radius, cells_radius + 1):
            for dc in range(-cells_radius, cells_radius + 1):
                cell = (base_row + dr, base_col + dc)
                for feat in self.grid.get(cell, []):
                    oid = feat.get("properties", {}).get("OBJECTID")
                    if oid not in seen_ids:
                        seen_ids.add(oid)
                        candidates.append(feat)
        return candidates


# ── Polygon edge helpers ──────────────────────────────────────────────────────

def get_polygon_edges(geometry: dict) -> list:
    """
    Return list of (lat1, lon1, lat2, lon2) tuples — one per polygon edge.
    Handles Polygon and MultiPolygon geometry types.
    """
    if not geometry:
        return []
    gtype  = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    ring   = None
    if gtype == "Polygon" and coords:
        ring = coords[0]
    elif gtype == "MultiPolygon" and coords:
        ring = coords[0][0]
    if not ring or len(ring) < 2:
        return []
    return [
        (ring[i][1], ring[i][0], ring[i+1][1], ring[i+1][0])
        for i in range(len(ring) - 1)
    ]


def find_roads_adjacent_to_polygon(geometry: dict, road_features: list) -> list:
    """
    Return distinct road names where at least one polygon edge is within
    EDGE_CORNER_RADIUS_MI of the road's centerline.
    Only considers scored road classes (CARTOCODE 1-8).

    Performance: pre-filters roads by bounding-box before full distance check.
    """
    edges = get_polygon_edges(geometry)
    if not edges:
        return []

    # Polygon bounding box + buffer for pre-filtering
    buf = EDGE_CORNER_RADIUS_MI / 69.0  # ~miles to degrees (rough)
    all_lats = [e[0] for e in edges] + [e[2] for e in edges]
    all_lons = [e[1] for e in edges] + [e[3] for e in edges]
    min_lat = min(all_lats) - buf
    max_lat = max(all_lats) + buf
    min_lon = min(all_lons) - buf
    max_lon = max(all_lons) + buf

    # Edge midpoints (single sample per edge — fast approximation)
    edge_mids = [
        ((lat1 + lat2) / 2, (lon1 + lon2) / 2)
        for lat1, lon1, lat2, lon2 in edges
    ]

    road_names: set = set()
    for road in road_features:
        props     = road.get("properties", {})
        name      = (props.get("FULLNAME") or props.get("NAME") or "").strip()
        cartocode = str(props.get("CARTOCODE") or "11")
        if not name or cartocode not in ALL_SCORED_CARTOCODES:
            continue

        # Quick bounding box pre-filter using road coordinates
        geom   = road.get("geometry", {})
        gtype  = geom.get("type", "")
        rcoords = geom.get("coordinates", [])
        if gtype == "MultiLineString":
            rcoords = [pt for seg in rcoords for pt in seg]
        # Road bounding box
        if not rcoords:
            continue
        rlats = [c[1] for c in rcoords]
        rlons = [c[0] for c in rcoords]
        if (max(rlats) < min_lat or min(rlats) > max_lat or
                max(rlons) < min_lon or min(rlons) > max_lon):
            continue  # Road too far away

        # Full edge-midpoint distance check
        for mid_lat, mid_lon in edge_mids:
            if point_to_road_distance(mid_lat, mid_lon, road) <= EDGE_CORNER_RADIUS_MI:
                road_names.add(name)
                break

    return list(road_names)


# ── Scoring functions ─────────────────────────────────────────────────────────

def score_aadt(aadt: int) -> int:
    """
    Score a parcel based on the highest AADT of any nearby road.
    Gas station viability thresholds (industry standard):
      >50,000 AADT = excellent
      20,000-50,000 = good
      10,000-20,000 = moderate
      5,000-10,000  = marginal
      <5,000        = poor
    """
    if aadt is None or aadt <= 0: return 0
    if aadt >= 50000: return 100
    if aadt >= 30000: return 85
    if aadt >= 20000: return 70
    if aadt >= 10000: return 50
    if aadt >= 5000:  return 30
    if aadt >= 2000:  return 15
    return 5


def score_arterial_access(nearest_dist_mi: float, road_class: str) -> int:
    """
    Score arterial access for mini-flex based on distance to nearest arterial.
    Mini-flex wants to be near (but not on) an arterial — good visibility + access.
    """
    if nearest_dist_mi > ARTERIAL_INFLUENCE_RADIUS_MI:
        return 0
    
    # Base score by road class
    class_scores = {
        "Interstate":         40,   # Too fast for mini-flex access
        "Other Freeway":      40,
        "Principal Arterial": 90,
        "Minor Arterial":     100,
        "Major Collector":    80,
        "Minor Collector":    60,
        "Local":              20,
    }
    base = class_scores.get(road_class, 50)
    
    # Distance decay
    if nearest_dist_mi < 0.05:   decay = 1.0
    elif nearest_dist_mi < 0.1:  decay = 0.9
    elif nearest_dist_mi < 0.15: decay = 0.75
    elif nearest_dist_mi < 0.2:  decay = 0.6
    else:                        decay = 0.4
    
    return int(base * decay)


def score_corner_lot(road_names: list) -> int:
    """
    Score corner lot status based on distinct named roads adjacent to the parcel.
    A true corner lot has 2+ distinct road names within CORNER_DETECTION_RADIUS_MI.
    """
    unique_roads = set(r for r in road_names if r)
    if len(unique_roads) >= 2:
        return 100   # True corner lot
    elif len(unique_roads) == 1:
        return 30    # Frontage on one road
    else:
        return 0     # No road adjacency detected


# ── Main analysis function ────────────────────────────────────────────────────

def analyze_road_adjacency(
    parcel_lat: float,
    parcel_lon: float,
    road_index: "RoadSpatialIndex",
    mode: str = "gas_station",
    parcel_geometry: dict = None,
) -> dict:
    """
    Analyze road adjacency for a single parcel centroid.
    Returns a dict with:
      - corner_score (0-100)
      - aadt_score (0-100)
      - arterial_score (0-100)
      - max_aadt (int)
      - nearest_arterial_dist_mi (float)
      - nearest_road_class (str)
      - adjacent_road_names (list)
    """
    # Search radius in degrees (0.01 deg ≈ 0.7 miles)
    search_radius_deg = 0.02  # ~1.4 miles — ensure we cover ARTERIAL_INFLUENCE_RADIUS_MI

    candidates = road_index.query_nearby(parcel_lat, parcel_lon, search_radius_deg)

    if not candidates:
        return {
            "corner_score":            0,
            "aadt_score":              0,
            "arterial_score":          0,
            "max_aadt":                0,
            "nearest_arterial_dist_mi": 99.0,
            "nearest_road_class":      "Unknown",
            "adjacent_road_names":     [],
        }

    # Compute distances to all candidate roads
    road_distances = []
    for feat in candidates:
        props = feat.get("properties", {})
        dist  = point_to_road_distance(parcel_lat, parcel_lon, feat)
        road_distances.append({
            "dist":       dist,
            "name":       props.get("FULLNAME") or props.get("NAME") or "",
            "cartocode":  str(props.get("CARTOCODE") or "11"),
            "fclass":     props.get("DOT_FCLASS") or "",
            "aadt":       int(props.get("DOT_AADT") or 0),
            "speed":      int(props.get("SPEED_LMT") or 0),
        })

    road_distances.sort(key=lambda x: x["dist"])

    # ── Corner detection ──────────────────────────────────────────────────────
    if parcel_geometry:
        # Edge-based detection: polygon edges must be within 30m of distinct named roads
        adjacent_names = find_roads_adjacent_to_polygon(parcel_geometry, candidates)
    else:
        # Legacy centroid-based fallback
        adjacent = [r for r in road_distances if r["dist"] <= CORNER_DETECTION_RADIUS_MI]
        adjacent_names = [r["name"] for r in adjacent if r["name"]]
    corner_score = score_corner_lot(adjacent_names)

    # ── AADT scoring ──────────────────────────────────────────────────────────
    # Use the highest AADT road within ARTERIAL_INFLUENCE_RADIUS_MI
    nearby_arterials = [
        r for r in road_distances
        if r["dist"] <= ARTERIAL_INFLUENCE_RADIUS_MI
        and r["cartocode"] in ALL_SCORED_CARTOCODES
    ]

    max_aadt = max((r["aadt"] for r in nearby_arterials), default=0)
    aadt_score = score_aadt(max_aadt)

    # ── Nearest arterial ──────────────────────────────────────────────────────
    arterials_only = [
        r for r in road_distances
        if r["cartocode"] in ARTERIAL_CARTOCODES
    ]

    if arterials_only:
        nearest_art = arterials_only[0]
        nearest_arterial_dist = nearest_art["dist"]
        nearest_road_class    = nearest_art["fclass"] or f"CARTOCODE-{nearest_art['cartocode']}"
        arterial_score = score_arterial_access(nearest_arterial_dist, nearest_art["fclass"])
    else:
        nearest_arterial_dist = 99.0
        nearest_road_class    = "Local"
        arterial_score = 0

    return {
        "corner_score":             corner_score,
        "aadt_score":               aadt_score,
        "arterial_score":           arterial_score,
        "max_aadt":                 max_aadt,
        "nearest_arterial_dist_mi": round(nearest_arterial_dist, 3),
        "nearest_road_class":       nearest_road_class,
        "adjacent_road_names":      list(set(adjacent_names))[:5],
    }


# ── Build county road indexes ─────────────────────────────────────────────────

def build_road_indexes(counties: list = None, force_refresh: bool = False) -> dict:
    """
    Fetch road data and build spatial indexes for each county.
    Returns dict: {county: RoadSpatialIndex}
    """
    if counties is None:
        counties = list(COUNTY_BBOXES.keys())

    roads_data = load_all_roads(counties, force_refresh=force_refresh)
    indexes = {}
    for county, features in roads_data.items():
        log.info(f"  Building spatial index for {county.title()} ({len(features):,} road segments)...")
        indexes[county] = RoadSpatialIndex(features)
        log.info(f"    Index built: {len(indexes[county].grid):,} grid cells")
    return indexes


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    log.info("Building road indexes for Davis and Weber counties...")
    indexes = build_road_indexes()

    # Test: 879 W Hill Field Rd, Layton (known commercial parcel from previous test)
    # Approximate coordinates
    test_parcels = [
        ("879 W Hill Field Rd, Layton (Commercial)", 41.0700, -111.9900),
        ("Ogden Commercial Area", 41.2200, -111.9700),
        ("Clearfield Commercial", 41.1100, -112.0100),
    ]

    for name, lat, lon in test_parcels:
        result = analyze_road_adjacency(lat, lon, indexes.get("davis", indexes.get("weber")))
        print(f"\n{name}:")
        print(f"  Corner Score:     {result['corner_score']}")
        print(f"  AADT Score:       {result['aadt_score']} (max AADT: {result['max_aadt']:,})")
        print(f"  Arterial Score:   {result['arterial_score']}")
        print(f"  Nearest Arterial: {result['nearest_arterial_dist_mi']:.3f} mi ({result['nearest_road_class']})")
        print(f"  Adjacent Roads:   {result['adjacent_road_names']}")
