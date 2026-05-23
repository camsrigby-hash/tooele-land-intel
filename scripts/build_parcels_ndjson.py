"""build_parcels_ndjson.py -- Phase 14-3 (updated Phase 14a)

Joins parcel polygon CSVs + D1 attribute export -> NDJSON of GeoJSON features
with baked attributes, ready to feed tippecanoe.

Polygon sources (in order):
  - data/raw/parcels_<county>.csv          (plain CSV, in git)
  - data/raw/parcels_<county>.csv.gz       (gzip, in git -- utah, weber)
  - <large-parcels-dir>/parcels_salt_lake.csv.gz  (GH Release large-parcels)

D1 attribute export format (either):
  - CSV with header row: parcel_id, corner_score, aadt_score,
    commute_corridor_score, vacancy_class, median_income,
    zone_current, zone_current_source, zone_future, zone_future_source,
    flu_source_jurisdiction, flu_plan_vintage, flu_currency_note,
    zone_current_normalized, zone_future_normalized, zone_future_secondary
  - Wrangler JSON: output of `wrangler d1 execute ... --json`
    i.e. [{"results": [{...}, ...], "success": true, "meta": {...}}]

Baked attributes in output features:
  parcel_id, acreage, prop_class, county,
  corner_score, aadt_score, commute_corridor_score,
  vacancy_class, median_income,
  zone_current, zone_current_source, zone_future, zone_future_source,
  flu_source_jurisdiction, flu_plan_vintage, flu_currency_note,
  zone_current_normalized, zone_future_normalized, zone_future_secondary

Phase 14a change: zoning_score (prop_class fallback from 13b-5) removed.
Real zoning columns from migrations 0008 (18b-3) + 0009 (18b-2e) added instead.

Usage:
  # local test (small counties, no D1 attrs):
  python scripts/build_parcels_ndjson.py \\
    --d1-export data/d1_parcel_attrs.csv \\
    --output /tmp/parcels.ndjson

  # full run with wrangler JSON export + salt_lake download:
  python scripts/build_parcels_ndjson.py \\
    --d1-export data/d1_parcel_attrs.json \\
    --download-large-parcels \\
    --output parcels.ndjson
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import subprocess
import sys
from pathlib import Path

# ---- Constants ---------------------------------------------------------------

RAW_DIR = Path("data/raw")
TOOELE_LAND_INTEL_REPO = "camsrigby-hash/tooele-land-intel"
LARGE_PARCELS_TAG = "large-parcels"

# Small enough to live in git (plain CSV or gzip <100 MB compressed)
GIT_PLAIN_CSV = [
    "parcels_box_elder.csv",
    "parcels_davis.csv",
    "parcels_tooele.csv",
    "parcels_wasatch.csv",
]
GIT_GZ_CSV = [
    "parcels_utah.csv.gz",
    "parcels_weber.csv.gz",
]
# Too large for git even compressed -- lives in GH Release large-parcels
RELEASE_GZ_CSV = [
    "parcels_salt_lake.csv.gz",
]

# D1 enrichment columns baked into tiles.
# Phase 14a: removed zoning_score (prop_class fallback, superseded by real
# zoning data from Phase 18b-3 + 18b-2e migrations 0008/0009).
D1_ATTR_COLS = [
    # Scoring dimensions baked into tiles (growth/STIP/competition/vacancy
    # score dims stay as setFeatureState overlays — not baked here)
    "corner_score",
    "aadt_score",
    "commute_corridor_score",
    "vacancy_class",
    "median_income",
    # Phase 18b-3 (migration 0008) — per-parcel zoning columns
    "zone_current",
    "zone_current_source",
    "zone_future",
    "zone_future_source",
    "flu_source_jurisdiction",
    "flu_plan_vintage",
    "flu_currency_note",
    # Phase 18b-2e (migration 0009) — taxonomy-harmonized normalized columns
    "zone_current_normalized",
    "zone_future_normalized",
    "zone_future_secondary",
]


# ---- D1 export loader --------------------------------------------------------

def load_d1_attrs(path: Path) -> dict[str, dict]:
    """Load D1 attribute export -> dict keyed by parcel_id.

    Accepts plain CSV (any extension except .json) or wrangler JSON output.
    Returns empty dict if the file is missing or empty.
    """
    if not path.exists() or path.stat().st_size == 0:
        print(
            f"  d1-export '{path}' is empty or missing -- proceeding with no attribute data",
            file=sys.stderr,
        )
        return {}

    if path.suffix.lower() == ".json":
        return _load_wrangler_json(path)
    return _load_csv(path)


def _load_csv(path: Path) -> dict[str, dict]:
    attrs: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("parcel_id") or "").strip()
            if pid:
                attrs[pid] = _extract_attrs(row)
    return attrs


def _load_wrangler_json(path: Path) -> dict[str, dict]:
    """Parse wrangler d1 execute --json output.

    Format: [{"results": [...], "success": true, "meta": {...}}]
    """
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    rows: list[dict] = []
    if isinstance(payload, list):
        for obj in payload:
            rows.extend(obj.get("results", []))
    elif isinstance(payload, dict):
        rows = payload.get("results", [])

    attrs: dict[str, dict] = {}
    for row in rows:
        pid = (row.get("parcel_id") or "").strip()
        if pid:
            attrs[pid] = _extract_attrs(row)
    return attrs


def _extract_attrs(row: dict) -> dict:
    return {
        # Numeric score dims
        "corner_score": _float_or_none(row.get("corner_score")),
        "aadt_score": _float_or_none(row.get("aadt_score")),
        "commute_corridor_score": _float_or_none(row.get("commute_corridor_score")),
        # String / categorical dims
        "vacancy_class": row.get("vacancy_class") or None,
        "median_income": _int_or_none(row.get("median_income")),
        # Phase 18b-3 zoning columns (migration 0008)
        "zone_current": row.get("zone_current") or None,
        "zone_current_source": row.get("zone_current_source") or None,
        "zone_future": row.get("zone_future") or None,
        "zone_future_source": row.get("zone_future_source") or None,
        "flu_source_jurisdiction": row.get("flu_source_jurisdiction") or None,
        "flu_plan_vintage": row.get("flu_plan_vintage") or None,
        "flu_currency_note": row.get("flu_currency_note") or None,
        # Phase 18b-2e normalized columns (migration 0009)
        "zone_current_normalized": row.get("zone_current_normalized") or None,
        "zone_future_normalized": row.get("zone_future_normalized") or None,
        "zone_future_secondary": row.get("zone_future_secondary") or None,
    }


# ---- Type coercion -----------------------------------------------------------

def _float_or_none(v) -> float | None:
    if v is None or str(v).strip() in ("", "None", "null"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int_or_none(v) -> int | None:
    if v is None or str(v).strip() in ("", "None", "null"):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# ---- CSV iteration -----------------------------------------------------------

def iter_csv(path: Path):
    """Yield DictReader rows from a plain or gzipped CSV.

    Raises field_size_limit to handle large polygon_geojson columns.
    """
    csv.field_size_limit(10 * 1024 * 1024)  # 10 MB per field
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield from csv.DictReader(f)
    else:
        with open(path, newline="", encoding="utf-8") as f:
            yield from csv.DictReader(f)


# ---- Feature builder ---------------------------------------------------------

def make_feature(row: dict, attrs: dict[str, dict]) -> dict | None:
    """Return a GeoJSON Feature dict or None if the row is unusable."""
    pid = (row.get("parcel_id") or "").strip()
    if not pid:
        return None

    polygon_raw = (row.get("polygon_geojson") or "").strip()
    if not polygon_raw:
        return None

    try:
        geometry = json.loads(polygon_raw)
    except (json.JSONDecodeError, ValueError):
        return None

    parcel_d1 = attrs.get(pid, {})
    properties: dict = {
        "parcel_id": pid,
        "acreage": _float_or_none(row.get("acreage")),
        "prop_class": row.get("prop_class") or None,
        "county": row.get("county") or None,
        **parcel_d1,
    }

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


# ---- GH Release downloader ---------------------------------------------------

def download_large_parcels(dest_dir: Path) -> None:
    """Download RELEASE_GZ_CSV assets from the large-parcels GH release."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for filename in RELEASE_GZ_CSV:
        dest = dest_dir / filename
        if dest.exists():
            print(f"  {filename}: already present, skipping download")
            continue
        print(f"  Downloading {filename} from {TOOELE_LAND_INTEL_REPO}@{LARGE_PARCELS_TAG}...")
        subprocess.run(
            [
                "gh", "release", "download", LARGE_PARCELS_TAG,
                "--repo", TOOELE_LAND_INTEL_REPO,
                "--pattern", filename,
                "--dir", str(dest_dir),
            ],
            check=True,
        )
        sz_mb = dest.stat().st_size // 1_000_000
        print(f"  {filename}: done ({sz_mb} MB)")


# ---- Source list builder -----------------------------------------------------

def build_source_list(raw_dir: Path, large_dir: Path) -> list[Path]:
    sources: list[Path] = []
    for name in GIT_PLAIN_CSV + GIT_GZ_CSV:
        p = raw_dir / name
        if p.exists():
            sources.append(p)
        else:
            print(f"  WARNING: {p} not found -- skipping", file=sys.stderr)
    for name in RELEASE_GZ_CSV:
        p = large_dir / name
        if p.exists():
            sources.append(p)
        else:
            print(
                f"  WARNING: {p} not found"
                " -- run with --download-large-parcels to fetch it",
                file=sys.stderr,
            )
    return sources


# ---- Main --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build parcel NDJSON for tippecanoe from polygon CSVs + D1 export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--d1-export", required=True, metavar="PATH",
        help=(
            "D1 parcel_records attribute export (CSV or wrangler JSON). "
            "Pass an empty file to emit polygons with no enrichment attrs."
        ),
    )
    parser.add_argument(
        "--output", default="parcels.ndjson", metavar="PATH",
        help="Output NDJSON file (default: parcels.ndjson)",
    )
    parser.add_argument(
        "--download-large-parcels", action="store_true",
        help=(
            "Download large-parcels GH release assets before processing "
            "(requires gh CLI + auth)"
        ),
    )
    parser.add_argument(
        "--large-parcels-dir", default=str(RAW_DIR), metavar="DIR",
        help=f"Directory for large-parcels assets (default: {RAW_DIR})",
    )
    parser.add_argument(
        "--raw-dir", default=str(RAW_DIR), metavar="DIR",
        help=f"Directory containing git-tracked parcel CSVs (default: {RAW_DIR})",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print per-county and per-attribute fill-rate stats at the end",
    )
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    large_dir = Path(args.large_parcels_dir)

    # 1. Optionally download large release assets
    if args.download_large_parcels:
        print("Downloading large-parcels release assets...")
        download_large_parcels(large_dir)

    # 2. Load D1 attribute lookup
    d1_export_path = Path(args.d1_export)
    print(f"Loading D1 attributes from {d1_export_path}...")
    d1_attrs = load_d1_attrs(d1_export_path)
    print(f"  {len(d1_attrs):,} parcel attribute records loaded")

    # 3. Build polygon source list
    sources = build_source_list(raw_dir, large_dir)
    if not sources:
        print("ERROR: no polygon CSV sources found -- nothing to do", file=sys.stderr)
        sys.exit(1)

    # 4. Iterate, join, emit NDJSON
    print(f"Writing NDJSON to {args.output} from {len(sources)} source(s)...")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_written = 0
    total_matched = 0
    county_stats: dict[str, dict] = {}

    with open(out_path, "w", encoding="utf-8") as out_f:
        for src in sources:
            src_rows = src_written = src_matched = 0
            county_name = (
                src.name
                .replace("parcels_", "")
                .replace(".csv.gz", "")
                .replace(".csv", "")
            )
            print(f"  {src.name}...", end="", flush=True)

            for row in iter_csv(src):
                src_rows += 1
                feature = make_feature(row, d1_attrs)
                if feature is None:
                    continue
                src_written += 1
                if feature["properties"]["parcel_id"] in d1_attrs:
                    src_matched += 1
                out_f.write(json.dumps(feature, separators=(",", ":")) + "\n")

            print(
                f" {src_rows:,} rows -> {src_written:,} features"
                f" ({src_matched:,} with D1 attrs)"
            )
            total_written += src_written
            total_matched += src_matched
            county_stats[county_name] = {
                "rows": src_rows,
                "written": src_written,
                "matched": src_matched,
            }

    pct = (total_matched / total_written * 100) if total_written else 0.0
    print(
        f"\nDone. {total_written:,} features written to {out_path}"
        f" ({total_matched:,} with D1 attrs, {pct:.1f}% match rate)"
    )

    if args.stats:
        print("\nPer-county stats:")
        print(f"  {'county':<20} {'rows':>8} {'written':>8} {'d1_match':>8} {'match%':>7}")
        for county, s in sorted(county_stats.items()):
            pct_c = (s["matched"] / s["written"] * 100) if s["written"] else 0.0
            print(
                f"  {county:<20} {s['rows']:>8,} {s['written']:>8,}"
                f" {s['matched']:>8,} {pct_c:>6.1f}%"
            )

        print("\nD1 attribute fill rates (across all written features):")
        col_counts: dict[str, int] = {c: 0 for c in D1_ATTR_COLS}
        feature_count = 0
        with open(out_path, encoding="utf-8") as rf:
            for line in rf:
                line = line.strip()
                if not line:
                    continue
                feature_count += 1
                props = json.loads(line)["properties"]
                for col in D1_ATTR_COLS:
                    if props.get(col) is not None:
                        col_counts[col] += 1
        for col in D1_ATTR_COLS:
            pct_a = (col_counts[col] / feature_count * 100) if feature_count else 0.0
            print(
                f"  {col:<30} {col_counts[col]:>8,} / {feature_count:>8,}"
                f"  ({pct_a:.1f}%)"
            )


if __name__ == "__main__":
    main()
