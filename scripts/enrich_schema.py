#!/usr/bin/env python3
"""One-time migration: add Phase 1 CM_RE signal columns to existing agenda_items_split.csv.

Derives signal_type from existing item_type via keyword mapping.
All other new columns default to empty string.
Idempotent: rows already having signal_type keep their values.
"""
import csv, json, sys, os
from pathlib import Path

ROOT = Path(os.path.expanduser("~/code/tooele-land-intel"))
CSV_PATH = ROOT / "data" / "agenda_items_split.csv"

ITEM_TYPE_TO_SIGNAL = {
    "residential_subdivision": "NEW_SUBDIVISION",
    "residential_density":     "NEW_SUBDIVISION",
    "commercial":              "COMMERCIAL_PROJECT",
    "industrial":              "COMMERCIAL_PROJECT",
    "mixed_use":               "COMMERCIAL_PROJECT",
    "rezone":                  "REZONE",
    "general_plan_amendment":  "GENERAL_PLAN_AMENDMENT",
    "conditional_use":         "COMMERCIAL_PROJECT",
    "site_plan":               "COMMERCIAL_PROJECT",
    "annexation":              "ANNEXATION",
    "infrastructure":          "INFRASTRUCTURE",
}

NEW_COLS = ["signal_type", "description", "location", "units", "developer",
            "zoning_from", "zoning_to", "status_enum", "growth_score", "notes"]

OUT_FIELDS = [
    "id", "jurisdiction", "body", "meeting_date", "title",
    "item_type", "confidence",
    "signal_type", "description", "location",
    "acres", "units", "developer", "zoning_from", "zoning_to",
    "status_enum", "growth_score", "notes",
    "url", "agenda_text", "source", "scraped_at",
]


def extract_extras(agenda_text_val):
    try:
        if agenda_text_val and str(agenda_text_val).strip().startswith("{"):
            d = json.loads(agenda_text_val)
            return {
                "developer": str(d.get("applicant", "") or ""),
                "acres":     str(d.get("acres", "") or ""),
            }
    except Exception:
        pass
    return {}


def main():
    if not CSV_PATH.exists():
        print(f"Not found: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    migrated = 0
    out_rows = []

    for row in rows:
        extras = extract_extras(row.get("agenda_text", ""))

        for col in NEW_COLS:
            if col not in row:
                row[col] = ""

        if not row.get("signal_type"):
            row["signal_type"] = ITEM_TYPE_TO_SIGNAL.get(str(row.get("item_type", "")), "")
            migrated += 1

        if not row.get("developer") and extras.get("developer"):
            row["developer"] = extras["developer"]

        if not row.get("acres") and extras.get("acres"):
            row["acres"] = extras["acres"]

        if not row.get("status_enum"):
            row["status_enum"] = "PROPOSED"

        out_rows.append(row)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Migrated {migrated} rows, wrote {len(out_rows)} total to {CSV_PATH.name}")


if __name__ == "__main__":
    main()
