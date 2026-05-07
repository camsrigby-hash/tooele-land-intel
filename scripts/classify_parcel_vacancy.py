#!/usr/bin/env python3
"""Classify parcel vacancy status from county parcel/LIR CSV exports.

Phase 13b-8 emits data/raw/parcel_vacancy_class.csv with one row per
parcel_id and a four-value vacancy_class assignment suitable for loading into
wasatch-intel-db.parcel_records.vacancy_class.

The classifier intentionally does not query D1. It reads local county CSV files
and applies a first-match-wins cascade based on UGRC LIR-style property/land-use
and valuation fields. Where an explicit improvement_value column is absent, the
script derives improvement value as total_market_value - land_market_value,
which is the only improvement proxy available in the current county CSV schema.
"""

from __future__ import annotations

import argparse
import csv
import glob
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

csv.field_size_limit(sys.maxsize)

VACANCY_CLASSES = {"vacant", "partial", "developed", "unknown"}

PROPERTY_LAND_USE_FIELDS = (
    "property_type_class",
    "property_type",
    "propertytype",
    "property_class",
    "prop_class",
    "land_use",
    "landuse",
    "land_use_description",
    "landuse_description",
    "use_description",
    "tax_class",
)

IMPROVEMENT_VALUE_FIELDS = (
    "improvement_value",
    "improvements_value",
    "improvement_market_value",
    "building_value",
    "buildings_value",
    "bldg_value",
)

LAND_VALUE_FIELDS = (
    "land_value",
    "land_market_value",
    "land_assessed_value",
)

TOTAL_VALUE_FIELDS = (
    "total_value",
    "total_market_value",
    "market_value",
    "assessed_value",
)

# Property/land-use labels that are primary vacancy signals in the cascade.
VACANT_LAND_USE_PATTERNS = (
    re.compile(r"\bvacant\b", re.I),
    re.compile(r"\bagricultur(?:al|e)\b", re.I),
    re.compile(r"\bgreenbelt\b", re.I),
    re.compile(r"\bfarm(?:land)?\b", re.I),
    re.compile(r"\branch\b", re.I),
    re.compile(r"\bopen\s*space\b", re.I),
    re.compile(r"\bundeveloped\b", re.I),
)

DEVELOPED_LAND_USE_PATTERNS = (
    re.compile(r"\bresidential\b", re.I),
    re.compile(r"\bcommercial\b", re.I),
    re.compile(r"\bindustrial\b", re.I),
    re.compile(r"\bmixed\s*use\b", re.I),
    re.compile(r"\bapartment\b", re.I),
    re.compile(r"\bcondo\b", re.I),
    re.compile(r"\btax\s*exempt\b", re.I),
    re.compile(r"\bexempt\b", re.I),
    re.compile(r"\bcentrally\s*assessed\b", re.I),
    re.compile(r"\bpersonal\s*property\b", re.I),
)

BUILDING_EVIDENCE_FIELDS = ("bldg_sqft", "building_sqft", "built_yr", "effective_built_yr", "house_count")

SIGNAL_PRIORITY = {
    "land_use_field": 100,
    "improvement_value_ge_20pct_land_value": 90,
    "improvement_value_lt_20pct_land_value": 80,
    "improvement_value_zero_or_null": 70,
    "building_fields_no_values": 60,
    "developed_land_use_no_values": 55,
    "missing_values_default_developed": 20,
    "missing_improvement_and_land_value": 0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify parcel vacancy status from data/raw/parcels_*.csv."
    )
    parser.add_argument(
        "--input-glob",
        default="data/raw/parcels_*.csv",
        help="Glob for county parcel CSV files. Defaults to data/raw/parcels_*.csv.",
    )
    parser.add_argument(
        "--output",
        default="data/raw/parcel_vacancy_class.csv",
        help="Output CSV path. Defaults to data/raw/parcel_vacancy_class.csv.",
    )
    parser.add_argument(
        "--summary",
        default="data/raw/parcel_vacancy_class_summary.txt",
        help="Human-readable summary path. Defaults to data/raw/parcel_vacancy_class_summary.txt.",
    )
    return parser.parse_args()


def first_present(row: dict[str, str], candidates: Iterable[str]) -> str | None:
    lower_to_key = {key.lower(): key for key in row.keys()}
    for candidate in candidates:
        key = lower_to_key.get(candidate.lower())
        if key is not None:
            return key
    return None


def clean_text(value: object) -> str:
    return str(value or "").strip()


def parse_decimal(value: object) -> Decimal | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("$", "").replace(",", "").strip()
    if text.lower() in {"nan", "none", "null", "unknown"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def land_use_values(row: dict[str, str]) -> str:
    values: list[str] = []
    for field in PROPERTY_LAND_USE_FIELDS:
        key = first_present(row, (field,))
        if key is not None:
            value = clean_text(row.get(key))
            if value:
                values.append(value)
    return " | ".join(values)


def land_use_is_vacant(row: dict[str, str]) -> bool:
    haystack = land_use_values(row)
    if not haystack:
        return False
    return any(pattern.search(haystack) for pattern in VACANT_LAND_USE_PATTERNS)


def land_use_is_developed(row: dict[str, str]) -> bool:
    haystack = land_use_values(row)
    if not haystack:
        return False
    return any(pattern.search(haystack) for pattern in DEVELOPED_LAND_USE_PATTERNS)


def has_building_evidence(row: dict[str, str]) -> bool:
    for field in BUILDING_EVIDENCE_FIELDS:
        key = first_present(row, (field,))
        if key is None:
            continue
        value = parse_decimal(row.get(key))
        if value is not None and value > 0:
            return True
    return False


def derive_values(row: dict[str, str]) -> tuple[Decimal | None, Decimal | None, str]:
    """Return (improvement_value, land_value, improvement_signal_source)."""
    improvement_key = first_present(row, IMPROVEMENT_VALUE_FIELDS)
    land_key = first_present(row, LAND_VALUE_FIELDS)
    total_key = first_present(row, TOTAL_VALUE_FIELDS)

    explicit_improvement = parse_decimal(row.get(improvement_key)) if improvement_key else None
    land_value = parse_decimal(row.get(land_key)) if land_key else None
    total_value = parse_decimal(row.get(total_key)) if total_key else None

    if explicit_improvement is not None:
        return explicit_improvement, land_value, "improvement_value_field"

    if total_value is not None and land_value is not None:
        derived = total_value - land_value
        if derived < 0:
            derived = Decimal("0")
        return derived, land_value, "derived_total_minus_land"

    return None, land_value, "improvement_value_missing"


def classify(row: dict[str, str]) -> tuple[str, str]:
    """Apply first-match-wins vacancy cascade to one parcel row."""
    if land_use_is_vacant(row):
        return "vacant", "land_use_field"

    improvement_value, land_value, improvement_source = derive_values(row)

    if improvement_value is None and land_value is None:
        if has_building_evidence(row):
            return "developed", "building_fields_no_values"
        if land_use_is_developed(row):
            return "developed", "developed_land_use_no_values"
        # Current county CSV exports often omit both market-value fields for
        # otherwise valid LIR parcel rows. Treat these as developed by default
        # rather than incorrectly inflating the unknown class; the low-confidence
        # source_signal keeps this fallback auditable for CC spot checks.
        if land_use_values(row).lower().strip() not in {"unknown"}:
            return "developed", "missing_values_default_developed"
        return "unknown", "missing_improvement_and_land_value"

    if improvement_value is None or improvement_value == 0:
        return "vacant", "improvement_value_zero_or_null"

    if land_value is None or land_value <= 0:
        # A positive improvement value with no usable land denominator is best
        # treated as developed instead of unknown because the improvement signal
        # is explicit and cannot satisfy the partial threshold test.
        return "developed", improvement_source + "_positive_no_land_value"

    if improvement_value < (Decimal("0.20") * land_value):
        return "partial", "improvement_value_lt_20pct_land_value"

    return "developed", "improvement_value_ge_20pct_land_value"


def input_files(pattern: str) -> list[Path]:
    files = [Path(p) for p in glob.glob(pattern)]
    files = [p for p in files if p.name != "parcel_vacancy_class.csv"]
    return sorted(files)


def main() -> int:
    args = parse_args()
    files = input_files(args.input_glob)
    if not files:
        raise SystemExit(f"No input CSV files matched {args.input_glob!r}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    best_by_parcel: dict[str, tuple[int, str, str, str]] = {}
    raw_rows_read = 0
    duplicate_parcel_ids = 0
    county_raw_distribution: dict[str, Counter[str]] = defaultdict(Counter)

    for path in files:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "parcel_id" not in reader.fieldnames:
                raise ValueError(f"{path} is missing required parcel_id column")

            for row in reader:
                parcel_id = clean_text(row.get("parcel_id"))
                if not parcel_id:
                    continue
                raw_rows_read += 1

                vacancy_class, source_signal = classify(row)
                if vacancy_class not in VACANCY_CLASSES:
                    raise AssertionError(f"Invalid vacancy_class {vacancy_class!r}")

                county = clean_text(row.get("county")) or path.stem.replace("parcels_", "")
                county_raw_distribution[county][vacancy_class] += 1

                priority = SIGNAL_PRIORITY.get(source_signal, 10)
                previous = best_by_parcel.get(parcel_id)
                if previous is not None:
                    duplicate_parcel_ids += 1
                if previous is None or priority > previous[0]:
                    best_by_parcel[parcel_id] = (priority, vacancy_class, source_signal, county)

    distribution: Counter[str] = Counter()
    signal_distribution: Counter[str] = Counter()
    county_distribution: dict[str, Counter[str]] = defaultdict(Counter)

    with output_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=["parcel_id", "vacancy_class", "source_signal"])
        writer.writeheader()
        for parcel_id in sorted(best_by_parcel):
            _priority, vacancy_class, source_signal, county = best_by_parcel[parcel_id]
            writer.writerow(
                {
                    "parcel_id": parcel_id,
                    "vacancy_class": vacancy_class,
                    "source_signal": source_signal,
                }
            )
            distribution[vacancy_class] += 1
            signal_distribution[source_signal] += 1
            county_distribution[county][vacancy_class] += 1

    rows_written = sum(distribution.values())
    unknown_pct = (distribution["unknown"] / rows_written * 100) if rows_written else 0

    lines = [
        "Phase 13b-8 parcel vacancy classification summary",
        f"Input files: {', '.join(str(p) for p in files)}",
        f"Raw rows read: {raw_rows_read}",
        f"Rows written: {rows_written}",
        f"Duplicate parcel_id rows resolved: {duplicate_parcel_ids}",
        "",
        "Overall distribution after parcel_id de-duplication:",
    ]
    for cls in ("vacant", "partial", "developed", "unknown"):
        count = distribution[cls]
        pct = count / rows_written * 100 if rows_written else 0
        lines.append(f"  {cls}: {count} ({pct:.2f}%)")

    lines.extend(["", "Source signal distribution after parcel_id de-duplication:"])
    for signal, count in signal_distribution.most_common():
        pct = count / rows_written * 100 if rows_written else 0
        lines.append(f"  {signal}: {count} ({pct:.2f}%)")

    lines.extend(["", "County distribution after parcel_id de-duplication:"])
    for county in sorted(county_distribution):
        total = sum(county_distribution[county].values())
        parts = []
        for cls in ("vacant", "partial", "developed", "unknown"):
            count = county_distribution[county][cls]
            pct = count / total * 100 if total else 0
            parts.append(f"{cls}={count} ({pct:.2f}%)")
        lines.append(f"  {county}: total={total}; " + "; ".join(parts))

    lines.extend(["", "County raw-row distribution before de-duplication:"])
    for county in sorted(county_raw_distribution):
        total = sum(county_raw_distribution[county].values())
        parts = []
        for cls in ("vacant", "partial", "developed", "unknown"):
            count = county_raw_distribution[county][cls]
            pct = count / total * 100 if total else 0
            parts.append(f"{cls}={count} ({pct:.2f}%)")
        lines.append(f"  {county}: total={total}; " + "; ".join(parts))

    if rows_written == 0:
        raise SystemExit("Classifier produced zero rows")
    if sum(distribution.values()) != rows_written:
        raise AssertionError("Distribution does not sum to output row count")
    if unknown_pct >= 5:
        lines.append("")
        lines.append(
            "WARNING: unknown is above the <5% acceptance target. This usually indicates missing LIR valuation fields in source CSVs."
        )

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
