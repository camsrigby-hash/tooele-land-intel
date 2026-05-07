"""
join_census_acs.py — Phase 13b-6b: Census ACS spatial join (local utility)

Reads census_acs_blockgroups.csv (with boundary_geojson) and the county parcel
CSVs, performs a shapely point-in-polygon test for each unique parcel centroid,
and prints UPDATE SQL to stdout or writes it to a file.

This script is the local counterpart to the GitHub Actions workflow
wasatch-intel/.github/workflows/enrich_parcels_census_join.yml.
Use it for local validation, debugging, or dry-run output inspection.

Usage:
  python scripts/join_census_acs.py [--county COUNTY] [--out /path/to/output.sql]

  --county: one of tooele, salt_lake, utah, davis, weber, wasatch, box_elder
            default: tooele (smallest county, fast for testing)
  --out:    write UPDATE SQL to this file instead of stdout

Requirements:
  pip install shapely
"""

import argparse
import csv
import gzip
import json
import os
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)

DATA_DIR = Path("data/raw")
CENSUS_CSV = DATA_DIR / "census_acs_blockgroups.csv"

COUNTY_FIPS_MAP = {
    "tooele":    "045",
    "salt_lake": "035",
    "utah":      "049",
    "davis":     "011",
    "weber":     "057",
    "wasatch":   "051",
    "box_elder": "003",
}

CHUNK_SIZE = 500


def open_parcel_csv(county: str):
    gz_path = DATA_DIR / f"parcels_{county}.csv.gz"
    csv_path = DATA_DIR / f"parcels_{county}.csv"
    if gz_path.exists():
        return gzip.open(gz_path, "rt", encoding="utf-8")
    if csv_path.exists():
        return open(csv_path, newline="", encoding="utf-8")
    raise FileNotFoundError(
        f"No parcel CSV found for {county}. "
        f"Expected {gz_path} or {csv_path}."
    )


def run(county: str, out_path: str | None = None):
    try:
        from shapely.geometry import shape, Point
        from shapely.strtree import STRtree
    except ImportError:
        print("shapely is required: pip install shapely", file=sys.stderr)
        sys.exit(1)

    county_fips = COUNTY_FIPS_MAP[county]
    print(f"=== {county} (FIPS {county_fips}) ===", file=sys.stderr)

    # ── Load block groups ──────────────────────────────────────────────────────
    block_groups = []
    with open(CENSUS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("county_fips", "") != county_fips:
                continue
            boundary_str = row.get("boundary_geojson", "").strip()
            if not boundary_str:
                continue
            income_str = row.get("median_income", "").strip()
            try:
                income_val = int(income_str) if income_str else None
            except ValueError:
                income_val = None
            try:
                geom = shape(json.loads(boundary_str))
                block_groups.append((geom, income_val, row.get("geoid", "")))
            except Exception as e:
                print(f"  Skip geoid {row.get('geoid','?')}: {e}", file=sys.stderr)

    print(f"Loaded {len(block_groups)} block groups", file=sys.stderr)
    if not block_groups:
        print("No block groups with boundaries — nothing to join", file=sys.stderr)
        return

    polygons = [bg[0] for bg in block_groups]
    tree = STRtree(polygons)

    # ── Spatial join ───────────────────────────────────────────────────────────
    seen_pids: set[str] = set()
    updates: list[str] = []
    matched = unmatched = no_centroid = 0

    with open_parcel_csv(county) as f:
        for row in csv.DictReader(f):
            pid = (row.get("parcel_id") or "").strip()
            if not pid or pid in seen_pids:
                continue
            seen_pids.add(pid)

            try:
                lng = float(row.get("centroid_lng") or "")
                lat = float(row.get("centroid_lat") or "")
            except (ValueError, TypeError):
                no_centroid += 1
                continue

            pt = Point(lng, lat)
            income_val = None
            for idx in tree.query(pt):
                geom, inc, _ = block_groups[int(idx)]
                if geom.contains(pt):
                    income_val = inc
                    break

            if income_val is not None:
                pid_esc = pid.replace("'", "''")
                updates.append(
                    f"UPDATE parcel_records SET median_income = {income_val} "
                    f"WHERE id = '{pid_esc}';"
                )
                matched += 1
            else:
                unmatched += 1

    print(
        f"Join: {len(seen_pids)} unique parcels | "
        f"matched={matched} | unmatched={unmatched} | no_centroid={no_centroid}",
        file=sys.stderr,
    )

    # ── Output ─────────────────────────────────────────────────────────────────
    sql_text = "\n".join(updates)
    if out_path:
        Path(out_path).write_text(sql_text, encoding="utf-8")
        print(f"Wrote {len(updates)} UPDATE statements to {out_path}", file=sys.stderr)
    else:
        print(sql_text)

    return {"matched": matched, "unmatched": unmatched, "no_centroid": no_centroid}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Census ACS spatial join: parcel centroid → block group → median_income"
    )
    parser.add_argument(
        "--county",
        default="tooele",
        choices=list(COUNTY_FIPS_MAP.keys()),
        help="County to process (default: tooele)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write UPDATE SQL to this file (default: stdout)",
    )
    args = parser.parse_args()
    run(args.county, args.out)
