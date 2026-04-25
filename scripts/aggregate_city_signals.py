"""
aggregate_city_signals.py — City-level signal aggregator for Tooele Land Intel.

Reads data/agenda_items_split.csv, applies CM_RE-heritage weighted scoring
(signal type weights × status multipliers), and writes
data/city_signal_scores.json consumed by /api/digest and /api/developers.

Port of vendor/cm_re/scraper/aggregator.py — aggregation pattern only.
CRE-specific scorer logic (gas station, miniflex ranking) excluded per
CM_RE_INTEGRATION.md §3.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Weights (from CM_RE aggregator — do NOT modify without updating types.ts) ──

SIGNAL_WEIGHTS: dict[str, float] = {
    "REZONE":                 1.4,
    "NEW_SUBDIVISION":        1.0,
    "COMMERCIAL_PROJECT":     1.6,
    "MINIFLEX_OPPORTUNITY":   1.8,
    "INFRASTRUCTURE":         1.5,
    "ANNEXATION":             1.3,
    "GENERAL_PLAN_AMENDMENT": 1.2,
    "LARGE_PROJECT":          1.3,
    "DEVELOPER_ACTIVITY":     0.8,
}

STATUS_MULTIPLIERS: dict[str, float] = {
    "APPROVED":  1.0,
    "PROPOSED":  0.7,
    "TABLED":    0.5,
    "CONTINUED": 0.5,
    "DENIED":    0.1,
}


def _float(v: str) -> float | None:
    try:
        return float(v) if v and v not in ("nan", "None", "") else None
    except ValueError:
        return None


def score_row(row: dict) -> float:
    base        = _float(row.get("growth_score", "")) or 50.0
    type_weight = SIGNAL_WEIGHTS.get(row.get("signal_type", ""), 1.0)
    status_mult = STATUS_MULTIPLIERS.get(row.get("status_enum", ""), 0.7)
    return base * type_weight * status_mult


def aggregate(csv_path: Path) -> dict:
    city_data: dict = defaultdict(lambda: {
        "raw_score":     0.0,
        "signal_counts": defaultdict(int),
        "developers":    set(),
        "recent_dates":  [],
        "signals":       [],
    })

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            city = (row.get("jurisdiction") or "").strip()
            if not city:
                continue

            date = (row.get("meeting_date") or "").strip()
            if date:
                city_data[city]["recent_dates"].append(date)

            signal_type = (row.get("signal_type") or "").strip()
            if not signal_type:
                continue

            weighted = score_row(row)
            city_data[city]["raw_score"] += weighted
            city_data[city]["signal_counts"][signal_type] += 1

            dev = (row.get("developer") or "").strip()
            if dev and dev not in ("nan", "None"):
                city_data[city]["developers"].add(dev)

            city_data[city]["signals"].append({
                "id":             row.get("id", ""),
                "signal_type":    signal_type,
                "status":         row.get("status_enum", ""),
                "growth_score":   _float(row.get("growth_score", "")),
                "weighted_score": round(weighted, 1),
                "title":          row.get("title", ""),
                "date":           date,
                "developer":      dev or None,
                "acres":          _float(row.get("acres", "")),
                "units":          _float(row.get("units", "")),
            })

    all_scores = [d["raw_score"] for d in city_data.values() if d["raw_score"] > 0]
    max_score  = max(all_scores) if all_scores else 1.0

    results = {}
    for city, data in city_data.items():
        normalized   = round((data["raw_score"] / max_score) * 100, 1)
        signal_counts = dict(data["signal_counts"])
        total_signals = sum(signal_counts.values())

        if normalized >= 75:
            grade = "A"
        elif normalized >= 50:
            grade = "B"
        elif normalized >= 25:
            grade = "C"
        else:
            grade = "D"

        dates = sorted([d for d in data["recent_dates"] if d and d not in ("nan", "None")], reverse=True)
        most_recent = dates[0] if dates else None

        top_signals = sorted(
            data["signals"], key=lambda s: s.get("weighted_score", 0), reverse=True
        )[:20]

        results[city] = {
            "city":                 city,
            "growth_score":         normalized,
            "grade":                grade,
            "total_signals":        total_signals,
            "signal_counts":        signal_counts,
            "active_developers":    sorted(data["developers"])[:10],
            "most_recent_activity": most_recent,
            "top_signals":          top_signals,
        }

    return results


def main() -> None:
    repo_root = Path(__file__).parent.parent
    csv_path  = repo_root / "data" / "agenda_items_split.csv"
    out_path  = repo_root / "data" / "city_signal_scores.json"

    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found", file=sys.stderr)
        sys.exit(1)

    results = aggregate(csv_path)

    payload = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "cities_analyzed": len(results),
        "cities":          results,
    }

    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(f"Wrote {out_path}")
    for city, data in sorted(results.items(), key=lambda x: x[1]["growth_score"], reverse=True):
        print(f"  {city}: {data['growth_score']} ({data['grade']})  {data['total_signals']} signals")


if __name__ == "__main__":
    main()
