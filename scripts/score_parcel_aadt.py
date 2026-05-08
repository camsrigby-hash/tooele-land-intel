#!/usr/bin/env python3
"""Score parcel centroids by proximity to UDOT AADT road segments.

Phase 13b-4 assigns each parcel an AADT score from the nearest UDOT AADT-tagged
road segment within 100 meters of the parcel centroid. The script downloads and
caches the official UDOT AADT GeoJSON if it is missing, projects all road
segments and parcel centroids into Utah's UTM Zone 12N meter CRS, then uses a
Shapely STRtree to perform nearest-neighbor candidate lookups efficiently across
~1 million parcel rows.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from typing import Iterator

try:
    from pyproj import Transformer
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
except ImportError as exc:  # pragma: no cover - exercised only in clean envs
    raise SystemExit(
        "Missing required geospatial dependency. Install with: "
        "python -m pip install shapely pyproj"
    ) from exc

csv.field_size_limit(sys.maxsize)

AADT_SERVICE_URL = (
    "https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/"
    "AADT2024_Unrounded/FeatureServer/3/query"
)
DEFAULT_AADT_CACHE = pathlib.Path("data/cache/udot_aadt.geojson")
DEFAULT_OUTPUT = pathlib.Path("data/raw/parcel_aadt_scores.csv")
DEFAULT_PARCEL_GLOB = "data/raw/parcels_*.csv*"
MAX_DISTANCE_M = 100.0
PAGE_SIZE = 2000
WGS84 = "EPSG:4326"
UTAH_METERS = "EPSG:26912"  # NAD83 / UTM zone 12N; appropriate for Utah statewide work.


class AADTIndex:
    """Projected AADT geometries and parallel AADT values."""

    def __init__(self, lines: list[LineString], aadts: list[float]) -> None:
        if not lines:
            raise ValueError("Cannot build an AADT index with zero geometries")
        self.lines = lines
        self.aadts = aadts
        self.tree = STRtree(lines)

    def nearest_within(self, point: Point, max_distance_m: float) -> tuple[float, float | None, float | None]:
        """Return (score, nearest_aadt, distance_m) for the nearest line within max distance."""
        candidate_indices = self.tree.query(point.buffer(max_distance_m))
        best_idx: int | None = None
        best_dist: float | None = None
        for raw_idx in candidate_indices:
            idx = int(raw_idx)
            dist = float(point.distance(self.lines[idx]))
            if dist <= max_distance_m and (best_dist is None or dist < best_dist):
                best_idx = idx
                best_dist = dist
        if best_idx is None or best_dist is None:
            return 0.0, None, None
        aadt = self.aadts[best_idx]
        return score_for_aadt(aadt), aadt, best_dist


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score parcel centroids by nearest UDOT AADT segment.")
    parser.add_argument("--parcel-glob", default=DEFAULT_PARCEL_GLOB, help="Glob for parcel CSV/CSV.GZ files.")
    parser.add_argument("--aadt-cache", type=pathlib.Path, default=DEFAULT_AADT_CACHE, help="Cached UDOT AADT GeoJSON path.")
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT, help="Output CSV path.")
    parser.add_argument("--max-distance-m", type=float, default=MAX_DISTANCE_M, help="Maximum road distance in meters.")
    parser.add_argument("--force-download", action="store_true", help="Re-download the UDOT AADT cache before scoring.")
    parser.add_argument("--limit", type=int, default=0, help="Optional parcel row limit for local smoke tests.")
    parser.add_argument("--progress-every", type=int, default=100000, help="Print progress every N parcels.")
    return parser.parse_args()


def fetch_arcgis(params: dict[str, str | int]) -> dict:
    url = AADT_SERVICE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=180) as response:
        payload = response.read()
    return json.loads(payload)


def download_aadt_geojson(path: pathlib.Path) -> None:
    """Download all UDOT AADT features with ArcGIS REST pagination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count_response = fetch_arcgis({"where": "1=1", "returnCountOnly": "true", "f": "json"})
    expected = int(count_response["count"])
    features: list[dict] = []
    base_params: dict[str, str | int] = {
        "where": "1=1",
        "outFields": "*",
        "f": "geojson",
        "outSR": "4326",
        "returnGeometry": "true",
        "orderByFields": "OBJECTID ASC",
        "resultRecordCount": PAGE_SIZE,
    }
    for offset in range(0, expected, PAGE_SIZE):
        params = dict(base_params)
        params["resultOffset"] = offset
        page = fetch_arcgis(params)
        page_features = page.get("features") or []
        if not page_features:
            raise RuntimeError(f"No AADT features returned at ArcGIS offset {offset}: {page}")
        features.extend(page_features)
        print(f"Downloaded {len(features):,}/{expected:,} UDOT AADT features", flush=True)
    if len(features) != expected:
        raise RuntimeError(f"Expected {expected:,} AADT features but downloaded {len(features):,}")
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")


def ensure_aadt_cache(path: pathlib.Path, force: bool = False) -> None:
    if not force and path.exists() and path.stat().st_size > 1_000_000:
        try:
            with path.open(encoding="utf-8") as fh:
                obj = json.load(fh)
            if obj.get("type") == "FeatureCollection" and len(obj.get("features") or []) >= 4000:
                return
        except Exception:
            pass
    download_aadt_geojson(path)


def aadt_value(properties: dict) -> float | None:
    """Return the preferred current AADT value from feature properties."""
    for key in ("AADT2024", "aadt2024", "AADT_2024"):
        value = properties.get(key)
        if value not in (None, ""):
            try:
                count = float(value)
            except (TypeError, ValueError):
                return None
            return count if count > 0 else None
    candidates: list[tuple[int, float]] = []
    for key, value in properties.items():
        if not str(key).upper().startswith("AADT") or value in (None, ""):
            continue
        try:
            year = int("".join(ch for ch in str(key) if ch.isdigit()))
            count = float(value)
        except ValueError:
            continue
        if count > 0:
            candidates.append((year, count))
    return max(candidates)[1] if candidates else None


def iter_lines(geometry: dict) -> Iterator[list[tuple[float, float]]]:
    if not geometry:
        return
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        yield [(float(lon), float(lat)) for lon, lat, *_ in coords]
    elif geom_type == "MultiLineString":
        for line in coords:
            yield [(float(lon), float(lat)) for lon, lat, *_ in line]


def load_aadt_index(aadt_path: pathlib.Path) -> AADTIndex:
    """Load UDOT AADT GeoJSON into projected Shapely lines and an STRtree."""
    with aadt_path.open(encoding="utf-8") as fh:
        collection = json.load(fh)
    transformer = Transformer.from_crs(WGS84, UTAH_METERS, always_xy=True)
    lines: list[LineString] = []
    aadts: list[float] = []
    feature_count = 0
    for feature in collection.get("features") or []:
        aadt = aadt_value(feature.get("properties") or {})
        if aadt is None or aadt <= 0:
            continue
        for lonlat_line in iter_lines(feature.get("geometry") or {}):
            if len(lonlat_line) < 2:
                continue
            projected = [transformer.transform(lon, lat) for lon, lat in lonlat_line]
            line = LineString(projected)
            if line.length <= 0:
                continue
            lines.append(line)
            aadts.append(aadt)
            feature_count += 1
    print(f"Loaded {feature_count:,} projected AADT line geometries from {aadt_path}")
    return AADTIndex(lines, aadts)


def score_for_aadt(aadt: float | None) -> float:
    if aadt is None:
        return 0.0
    if aadt >= 40000:
        return 1.0
    if aadt >= 20000:
        return 0.7
    if aadt >= 5000:
        return 0.4
    if aadt > 0:
        return 0.1
    return 0.0


def open_parcel_csv(path: pathlib.Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return path.open("r", newline="", encoding="utf-8")


def discover_parcel_files(pattern: str) -> list[pathlib.Path]:
    files = sorted(pathlib.Path().glob(pattern))
    files = [p for p in files if p.name != DEFAULT_OUTPUT.name]
    if not files:
        raise FileNotFoundError(f"No parcel CSV files matched {pattern!r}")
    return files


def write_scores(args: argparse.Namespace, aadt_index: AADTIndex) -> dict[str, object]:
    parcel_files = discover_parcel_files(args.parcel_glob)
    transformer = Transformer.from_crs(WGS84, UTAH_METERS, always_xy=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    skipped = 0
    duplicate_parcel_ids = 0
    seen_parcel_ids: set[str] = set()
    score_counts: Counter[str] = Counter()
    county_counts: Counter[str] = Counter()
    t0 = time.time()
    with args.output.open("w", newline="", encoding="utf-8") as out_fh:
        writer = csv.DictWriter(out_fh, fieldnames=["parcel_id", "aadt_score", "nearest_aadt_value", "distance_m"])
        writer.writeheader()
        for parcel_path in parcel_files:
            with open_parcel_csv(parcel_path) as in_fh:
                reader = csv.DictReader(in_fh)
                required = {"parcel_id", "centroid_lng", "centroid_lat"}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError(f"{parcel_path} is missing one of {sorted(required)}")
                for row in reader:
                    if args.limit and total >= args.limit:
                        break
                    parcel_id = (row.get("parcel_id") or "").strip()
                    if not parcel_id:
                        skipped += 1
                        continue
                    if parcel_id in seen_parcel_ids:
                        duplicate_parcel_ids += 1
                        continue
                    seen_parcel_ids.add(parcel_id)
                    try:
                        lon = float(row.get("centroid_lng") or "")
                        lat = float(row.get("centroid_lat") or "")
                    except ValueError:
                        skipped += 1
                        continue
                    x, y = transformer.transform(lon, lat)
                    score, aadt, distance = aadt_index.nearest_within(Point(x, y), args.max_distance_m)
                    writer.writerow(
                        {
                            "parcel_id": parcel_id,
                            "aadt_score": f"{score:.1f}",
                            "nearest_aadt_value": "" if aadt is None else str(int(round(aadt))),
                            "distance_m": "" if distance is None else f"{distance:.2f}",
                        }
                    )
                    total += 1
                    score_counts[f"{score:.1f}"] += 1
                    county_counts[(row.get("county") or parcel_path.stem.replace("parcels_", "")).strip()] += 1
                    if args.progress_every and total % args.progress_every == 0:
                        elapsed = time.time() - t0
                        print(f"Scored {total:,} parcels in {elapsed:,.1f}s", flush=True)
                if args.limit and total >= args.limit:
                    break
    return {
        "total": total,
        "skipped": skipped,
        "duplicate_parcel_ids": duplicate_parcel_ids,
        "score_counts": dict(score_counts),
        "county_counts": dict(county_counts),
    }


def main() -> None:
    args = parse_args()
    ensure_aadt_cache(args.aadt_cache, force=args.force_download)
    aadt_index = load_aadt_index(args.aadt_cache)
    summary = write_scores(args, aadt_index)
    total = int(summary["total"])
    positive = total - int(summary["score_counts"].get("0.0", 0))
    high = int(summary["score_counts"].get("0.7", 0)) + int(summary["score_counts"].get("1.0", 0))
    print(json.dumps(summary, indent=2, sort_keys=True))
    if total:
        print(f"Positive-score share: {positive / total:.2%}")
        print(f"High-traffic (>=0.7) share: {high / total:.2%}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
