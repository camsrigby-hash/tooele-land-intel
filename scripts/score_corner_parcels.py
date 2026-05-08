#!/usr/bin/env python3
"""Score Utah parcels for corner-road exposure.

Phase 13b-3 scorer for Wasatch Intel / Tooele Land Intel.

The script reads parcel CSVs from data/raw/parcels_<county>.csv(.gz), downloads
UGRC Utah Roads centerlines by county extent, and writes
`data/raw/parcel_corner_scores.csv` with:

    parcel_id, corner_score, road_count, top_road_class

Scoring is based on distinct named road centerlines within a configurable
meter distance of the parcel polygon boundary. DOT_FCLASS from UGRC roads is
used to distinguish arterial/collector roads from local roads.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gzip
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from shapely import from_geojson
from shapely.geometry import LineString, MultiLineString, shape
from shapely.ops import transform
from shapely.strtree import STRtree

csv.field_size_limit(sys.maxsize)

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
ROAD_CACHE_DIR = REPO_ROOT / "data" / "cache" / "ugrc_roads"
OUTPUT_CSV = RAW_DIR / "parcel_corner_scores.csv"

UGRC_ROADS_QUERY_URL = (
    "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/"
    "UtahRoads/FeatureServer/0/query"
)

# Approximate local projection constants for Utah. The scorer uses a fixed,
# meter-based equirectangular projection, which is precise enough for the
# 10-40 m frontage thresholds used here and avoids heavyweight GIS dependencies.
ORIGIN_LON = -111.9
ORIGIN_LAT = 40.5
METERS_PER_DEG_LAT = 111_132.0
METERS_PER_DEG_LON = 111_320.0 * math.cos(math.radians(ORIGIN_LAT))

HIGHWAY_CLASSES = {
    "Interstate",
    "Other Freeway",
    "Principal Arterial",
    "Minor Arterial",
    "Major Collector",
    "Minor Collector",
}
CLASS_PRIORITY = {
    "Interstate": 70,
    "Other Freeway": 65,
    "Principal Arterial": 60,
    "Minor Arterial": 50,
    "Major Collector": 40,
    "Minor Collector": 30,
    "Local": 10,
    None: 0,
    "": 0,
}

COUNTY_FILES = {
    "box_elder": "parcels_box_elder.csv",
    "davis": "parcels_davis.csv",
    "salt_lake": "parcels_salt_lake.csv.gz",
    "tooele": "parcels_tooele.csv",
    "utah": "parcels_utah.csv.gz",
    "wasatch": "parcels_wasatch.csv",
    "weber": "parcels_weber.csv.gz",
}


def project_xy(lon: float, lat: float) -> Tuple[float, float]:
    return ((lon - ORIGIN_LON) * METERS_PER_DEG_LON, (lat - ORIGIN_LAT) * METERS_PER_DEG_LAT)


def project_geom(geom):
    return transform(lambda x, y, z=None: ((x - ORIGIN_LON) * METERS_PER_DEG_LON, (y - ORIGIN_LAT) * METERS_PER_DEG_LAT), geom)


def open_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return open(path, "rt", newline="", encoding="utf-8")


def iter_parcel_rows(path: Path) -> Iterable[Dict[str, str]]:
    with open_csv(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def county_extent(path: Path, max_rows: Optional[int] = None) -> Tuple[float, float, float, float, int]:
    min_lon, min_lat, max_lon, max_lat = 999.0, 999.0, -999.0, -999.0
    count = 0
    for row in iter_parcel_rows(path):
        if max_rows and count >= max_rows:
            break
        count += 1
        try:
            lon = float(row.get("centroid_lng") or "nan")
            lat = float(row.get("centroid_lat") or "nan")
        except ValueError:
            continue
        if not (-125.0 <= lon <= -100.0 and 35.0 <= lat <= 45.0):
            continue
        min_lon, min_lat = min(min_lon, lon), min(min_lat, lat)
        max_lon, max_lat = max(max_lon, lon), max(max_lat, lat)
    if min_lon == 999.0:
        raise RuntimeError(f"No valid centroid extent found in {path}")
    return min_lon, min_lat, max_lon, max_lat, count


def chunked(seq: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def request_with_retry(params: Dict[str, Any], retries: int = 5, method: str = "GET") -> requests.Response:
    delay = 2.0
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if method.upper() == "POST":
                resp = requests.post(UGRC_ROADS_QUERY_URL, data=params, timeout=(15, 60))
            else:
                resp = requests.get(UGRC_ROADS_QUERY_URL, params=params, timeout=(15, 60))
            if resp.status_code == 200:
                return resp
            last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if attempt < retries:
            time.sleep(delay)
            delay *= 1.8
    raise RuntimeError(f"UGRC roads request failed after {retries} retries: {last_exc}")


def fetch_road_object_ids(envelope: Tuple[float, float, float, float]) -> List[int]:
    min_lon, min_lat, max_lon, max_lat = envelope
    params = {
        "f": "json",
        "where": "1=1",
        "returnIdsOnly": "true",
        "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    data = request_with_retry(params).json()
    if "error" in data:
        raise RuntimeError(f"UGRC road ID query error: {data['error']}")
    return sorted(set(data.get("objectIds") or []))


def fetch_road_feature_batch(batch: Sequence[int]) -> List[Dict[str, Any]]:
    params = {
        "f": "geojson",
        "objectIds": ",".join(map(str, batch)),
        "outFields": "OBJECTID,FULLNAME,NAME,DOT_HWYNAM,DOT_RTNAME,DOT_FCLASS,DOT_AADT,SPEED_LMT",
        "returnGeometry": "true",
        "outSR": "4326",
        "geometryPrecision": "6",
    }
    data = request_with_retry(params, method="POST").json()
    if "error" in data:
        raise RuntimeError(f"UGRC road feature query error: {data['error']}")
    return data.get("features", [])


def fetch_road_features(object_ids: Sequence[int]) -> List[Dict[str, Any]]:
    features: List[Dict[str, Any]] = []
    object_id_list = list(object_ids)
    batch_size = int(os.environ.get("UGRC_ROAD_BATCH_SIZE", "50"))
    workers = int(os.environ.get("UGRC_ROAD_WORKERS", "6"))
    batches = list(chunked(object_id_list, batch_size))
    print(f"  downloading roads in {len(batches):,} batches of {batch_size:,} with {workers:,} workers", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_no = {executor.submit(fetch_road_feature_batch, batch): i for i, batch in enumerate(batches, start=1)}
        for completed, future in enumerate(concurrent.futures.as_completed(future_to_no), start=1):
            batch_no = future_to_no[future]
            batch_features = future.result()
            features.extend(batch_features)
            if completed == 1 or completed % 100 == 0 or completed == len(batches):
                print(
                    f"  roads batches complete {completed:,}/{len(batches):,}; "
                    f"latest batch {batch_no:,}; features {len(features):,}",
                    flush=True,
                )
    return features


def load_or_download_roads(
    county: str,
    parcel_path: Path,
    pad_degrees: float,
    refresh: bool = False,
) -> Tuple[List[Any], List[Dict[str, Any]], Tuple[float, float, float, float]]:
    ROAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = ROAD_CACHE_DIR / f"{county}_roads.geojson"
    extent = county_extent(parcel_path)[:4]
    envelope = (
        extent[0] - pad_degrees,
        extent[1] - pad_degrees,
        extent[2] + pad_degrees,
        extent[3] + pad_degrees,
    )

    if refresh or not cache_path.exists():
        print(f"[{county}] downloading UGRC road IDs for envelope {envelope}", flush=True)
        object_ids = fetch_road_object_ids(envelope)
        print(f"[{county}] downloading {len(object_ids):,} UGRC road segments", flush=True)
        features = fetch_road_features(object_ids)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
    else:
        print(f"[{county}] using cached roads: {cache_path}", flush=True)
        with open(cache_path, "r", encoding="utf-8") as f:
            features = json.load(f).get("features", [])

    road_geoms: List[Any] = []
    road_props: List[Dict[str, Any]] = []
    for feat in features:
        try:
            geom = shape(feat.get("geometry"))
            if geom.is_empty or not isinstance(geom, (LineString, MultiLineString)):
                continue
            road_geoms.append(project_geom(geom))
            road_props.append(feat.get("properties") or {})
        except Exception:
            continue
    print(f"[{county}] indexed {len(road_geoms):,} projected road geometries", flush=True)
    return road_geoms, road_props, envelope


def normalize_road_name(props: Dict[str, Any]) -> str:
    for key in ("FULLNAME", "DOT_RTNAME", "DOT_HWYNAM", "NAME"):
        value = (props.get(key) or "").strip().upper()
        if value:
            return " ".join(value.split())
    return ""


def class_priority(road_class: Optional[str]) -> int:
    return CLASS_PRIORITY.get(road_class, 0)


def score_from_hits(named_hits: Dict[str, str]) -> Tuple[float, int, str]:
    if not named_hits:
        return 0.0, 0, ""

    road_count = len(named_hits)
    classes = list(named_hits.values())
    top_class = max(classes, key=class_priority) if classes else ""
    high_count = sum(1 for c in classes if c in HIGHWAY_CLASSES)

    if road_count >= 2:
        if high_count >= 2:
            return 1.0, road_count, top_class
        if high_count == 1:
            return 0.82, road_count, top_class
        # Two local roads normally indicate an ordinary residential corner lot.
        # Keep it below the >=0.7 acceptance threshold so high-scoring corners
        # remain concentrated around arterial/collector intersections.
        return 0.25, road_count, top_class

    only = classes[0]
    if only in {"Interstate", "Other Freeway", "Principal Arterial"}:
        return 0.40, road_count, only
    if only in {"Minor Arterial", "Major Collector", "Minor Collector"}:
        return 0.30, road_count, only
    if only == "Local":
        return 0.10, road_count, only
    return 0.05, road_count, only or ""


def parse_parcel_geom(row: Dict[str, str]):
    raw = row.get("polygon_geojson") or ""
    if not raw.strip():
        return None
    try:
        geom = from_geojson(raw)
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            return None
        return project_geom(geom)
    except Exception:
        return None


def score_parcel(row: Dict[str, str], tree: STRtree, road_geoms: List[Any], road_props: List[Dict[str, Any]], distance_m: float) -> Tuple[float, int, str]:
    geom = parse_parcel_geom(row)
    if geom is None:
        return 0.0, 0, ""

    boundary = geom.boundary
    if boundary.is_empty:
        return 0.0, 0, ""

    try:
        candidate_indices = tree.query(boundary, predicate="dwithin", distance=distance_m)
    except TypeError:
        candidate_indices = tree.query(boundary.buffer(distance_m))
    named_hits: Dict[str, str] = {}
    for idx in candidate_indices:
        idx_int = int(idx)
        road_geom = road_geoms[idx_int]
        try:
            if boundary.distance(road_geom) > distance_m:
                continue
        except Exception:
            continue
        props = road_props[idx_int]
        name = normalize_road_name(props)
        if not name:
            continue
        road_class = (props.get("DOT_FCLASS") or "").strip()
        existing = named_hits.get(name)
        if existing is None or class_priority(road_class) > class_priority(existing):
            named_hits[name] = road_class

    return score_from_hits(named_hits)


def score_county(
    county: str,
    parcel_path: Path,
    scored_rows: Dict[str, Tuple[float, int, str]],
    distance_m: float,
    pad_degrees: float,
    refresh_roads: bool,
    max_rows: Optional[int] = None,
) -> Counter:
    road_geoms, road_props, _ = load_or_download_roads(county, parcel_path, pad_degrees, refresh_roads)
    tree = STRtree(road_geoms)
    stats: Counter = Counter()

    for i, row in enumerate(iter_parcel_rows(parcel_path), start=1):
        if max_rows and i > max_rows:
            break
        parcel_id = (row.get("parcel_id") or "").strip()
        if not parcel_id:
            stats["missing_parcel_id"] += 1
            continue
        score, road_count, top_class = score_parcel(row, tree, road_geoms, road_props, distance_m)
        existing = scored_rows.get(parcel_id)
        candidate = (score, road_count, top_class)
        if existing is None:
            scored_rows[parcel_id] = candidate
        elif (score, road_count) > (existing[0], existing[1]):
            scored_rows[parcel_id] = candidate
            stats["duplicate_parcel_id_replaced"] += 1
        else:
            stats["duplicate_parcel_id_kept_existing"] += 1
        stats["rows"] += 1
        stats[f"score_{score:.2f}"] += 1
        if i % 25_000 == 0:
            print(f"[{county}] scored {i:,} parcels", flush=True)

    print(f"[{county}] complete: {stats['rows']:,} rows", flush=True)
    return stats


def available_counties() -> List[str]:
    return [c for c, name in COUNTY_FILES.items() if (RAW_DIR / name).exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Score parcel corner-road exposure from parcel polygons and UGRC roads.")
    parser.add_argument("--output", default=str(OUTPUT_CSV), help="Output CSV path")
    parser.add_argument("--counties", default=",".join(COUNTY_FILES.keys()), help="Comma-separated counties to score")
    parser.add_argument("--distance-m", type=float, default=25.0, help="Road-centerline-to-parcel-boundary distance threshold in meters")
    parser.add_argument("--pad-degrees", type=float, default=0.05, help="Envelope padding for road downloads")
    parser.add_argument("--refresh-roads", action="store_true", help="Re-download cached UGRC road extracts")
    parser.add_argument("--max-rows", type=int, default=None, help="Optional row limit per county for dry runs")
    args = parser.parse_args()

    requested = [c.strip() for c in args.counties.split(",") if c.strip()]
    missing = [c for c in requested if not (RAW_DIR / COUNTY_FILES.get(c, "")).exists()]
    if missing:
        raise SystemExit(f"Missing parcel files for counties: {missing}. Expected under {RAW_DIR}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = Counter()
    started = time.time()
    scored_rows: Dict[str, Tuple[float, int, str]] = {}
    for county in requested:
        parcel_path = RAW_DIR / COUNTY_FILES[county]
        print(f"=== scoring {county}: {parcel_path} ===", flush=True)
        stats = score_county(
            county=county,
            parcel_path=parcel_path,
            scored_rows=scored_rows,
            distance_m=args.distance_m,
            pad_degrees=args.pad_degrees,
            refresh_roads=args.refresh_roads,
            max_rows=args.max_rows,
        )
        total.update(stats)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["parcel_id", "corner_score", "road_count", "top_road_class"])
        for parcel_id in sorted(scored_rows):
            score, road_count, top_class = scored_rows[parcel_id]
            writer.writerow([parcel_id, f"{score:.2f}", road_count, top_class])

    elapsed = time.time() - started
    print("=== complete ===", flush=True)
    print(f"output={out_path}", flush=True)
    print(f"source_rows={total['rows']:,}", flush=True)
    print(f"deduped_rows={len(scored_rows):,}", flush=True)
    print(f"elapsed_seconds={elapsed:.1f}", flush=True)
    for key, value in sorted(total.items()):
        if key.startswith("score_"):
            print(f"{key}={value}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
