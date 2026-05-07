#!/usr/bin/env python3
"""Score parcel zoning fit for Phase 13b-5.

This script reads the county parcel CSVs in data/raw, applies the hand-curated
zoning normalizer in data/zoning_normalizer.yaml, and writes
parcel_id,zoning_score,normalized_class,source_zone_string to
 data/raw/parcel_zoning_scores.csv.

The primary path uses a zone_class / zoning / zone_code style source column if
one exists in the input CSV. The checked-in Phase 13b-2 UGRC LIR CSVs currently
lack those columns, so the script also supports an explicit property-class
fallback controlled by the normalizer file. The fallback is labeled in
normalized_class and source_zone_string so D1 consumers can distinguish it from
true machine-readable zoning.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import yaml

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "data" / "raw"
DEFAULT_NORMALIZER = ROOT / "data" / "zoning_normalizer.yaml"
DEFAULT_OUTPUT = ROOT / "data" / "raw" / "parcel_zoning_scores.csv"


def canonical(value: Any) -> str:
    """Return a tolerant comparison key for jurisdiction zoning strings."""
    if value is None:
        return ""
    s = str(value).strip()
    s = " ".join(s.split())
    return s.casefold()


def open_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def iter_input_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.glob("parcels_*.csv")):
        if path.name == "parcel_zoning_scores.csv":
            continue
        yield path
    for path in sorted(input_dir.glob("parcels_*.csv.gz")):
        yield path


def load_normalizer(path: Path) -> Tuple[Dict[str, Tuple[float, str]], Dict[str, Tuple[float, str]], list[str], set[str]]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    zone_map: Dict[str, Tuple[float, str]] = {}
    for normalized_class, block in (cfg.get("normalization") or {}).items():
        score = float(block["score"])
        for raw in block.get("strings") or []:
            key = canonical(raw)
            if key:
                zone_map[key] = (score, normalized_class)

    fallback_map: Dict[str, Tuple[float, str]] = {}
    for raw, block in (cfg.get("property_class_fallback") or {}).items():
        key = canonical(raw)
        fallback_map[key] = (float(block["score"]), str(block["normalized_class"]))

    source_columns = list(cfg.get("primary_source_columns") or ["zone_class", "zoning", "zone", "zone_code"])
    unscored = {canonical(v) for v in (cfg.get("unscored_values") or [])}
    return zone_map, fallback_map, source_columns, unscored


def choose_source_column(fieldnames: list[str], preferred: list[str]) -> Optional[str]:
    by_key = {canonical(name).replace(" ", "_"): name for name in fieldnames}
    for candidate in preferred:
        key = canonical(candidate).replace(" ", "_")
        if key in by_key:
            return by_key[key]
    for name in fieldnames:
        lowered = canonical(name)
        if "zone" in lowered or "zoning" in lowered:
            return name
    return None


def score_row(
    row: Dict[str, str],
    source_col: Optional[str],
    zone_map: Dict[str, Tuple[float, str]],
    fallback_map: Dict[str, Tuple[float, str]],
    unscored: set[str],
    allow_fallback: bool,
) -> Tuple[str, str, str]:
    """Return (score_text, normalized_class, source_zone_string)."""
    if source_col:
        source = (row.get(source_col) or "").strip()
        key = canonical(source)
        if key and key not in unscored and key in zone_map:
            score, normalized_class = zone_map[key]
            return f"{score:.1f}", normalized_class, source
        if key and key not in unscored:
            return "", "unmapped_zone_class", source

    if allow_fallback:
        prop = (row.get("prop_class") or "").strip()
        key = canonical(prop)
        if key not in unscored and key in fallback_map:
            score, normalized_class = fallback_map[key]
            return f"{score:.1f}", normalized_class, f"prop_class:{prop}"
        if key and key not in unscored:
            return "", "unmapped_property_class", f"prop_class:{prop}"

    return "", "no_machine_readable_zoning", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply generic commercial zoning-fit scores to parcel CSVs.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--normalizer", type=Path, default=DEFAULT_NORMALIZER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--disable-property-class-fallback",
        action="store_true",
        help="Only score true zone_class-like columns; do not use prop_class fallback.",
    )
    args = parser.parse_args()

    zone_map, fallback_map, source_columns, unscored = load_normalizer(args.normalizer)
    allow_fallback = not args.disable_property_class_fallback
    input_files = list(iter_input_files(args.input_dir))
    if not input_files:
        raise SystemExit(f"No parcel CSVs found in {args.input_dir}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    totals = Counter()
    by_file: dict[str, Counter] = defaultdict(Counter)
    source_cols: dict[str, str] = {}

    with open(args.output, "w", encoding="utf-8", newline="") as out_f:
        writer = csv.DictWriter(
            out_f,
            fieldnames=["parcel_id", "zoning_score", "normalized_class", "source_zone_string"],
        )
        writer.writeheader()

        for path in input_files:
            with open_csv(path) as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                source_col = choose_source_column(fieldnames, source_columns)
                source_cols[path.name] = source_col or ""

                for row in reader:
                    parcel_id = (row.get("parcel_id") or "").strip()
                    if not parcel_id:
                        continue
                    score, normalized_class, source = score_row(
                        row,
                        source_col,
                        zone_map,
                        fallback_map,
                        unscored,
                        allow_fallback,
                    )
                    writer.writerow(
                        {
                            "parcel_id": parcel_id,
                            "zoning_score": score,
                            "normalized_class": normalized_class,
                            "source_zone_string": source,
                        }
                    )
                    totals["rows"] += 1
                    by_file[path.name]["rows"] += 1
                    if score != "":
                        totals["scored"] += 1
                        by_file[path.name]["scored"] += 1
                    else:
                        totals[normalized_class] += 1
                        by_file[path.name][normalized_class] += 1

    coverage = (totals["scored"] / totals["rows"] * 100.0) if totals["rows"] else 0.0
    print(f"Wrote {args.output}")
    print(f"Rows: {totals['rows']:,}; scored: {totals['scored']:,}; coverage: {coverage:.2f}%")
    print("Source columns by file:")
    for name in sorted(source_cols):
        print(f"  {name}: {source_cols[name] or '(none; prop_class fallback used)' if allow_fallback else source_cols[name] or '(none)'}")
    print("Per-file coverage:")
    for name in sorted(by_file):
        rows = by_file[name]["rows"]
        scored = by_file[name]["scored"]
        pct = scored / rows * 100.0 if rows else 0.0
        print(f"  {name}: {scored:,}/{rows:,} ({pct:.2f}%)")
    for key in ["unmapped_zone_class", "unmapped_property_class", "no_machine_readable_zoning"]:
        if totals[key]:
            print(f"{key}: {totals[key]:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
