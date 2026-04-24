"""
aggregator.py — Growth Signal Aggregator
Rolls up individual document signals into area-level growth scores.
Produces the data that feeds the heat map and parcel scoring layers.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from config import JSON_DIR

log = logging.getLogger(__name__)

# Signal type weights — how much each type contributes to area growth score
SIGNAL_WEIGHTS = {
    "REZONE":                 1.4,
    "NEW_SUBDIVISION":        1.0,
    "COMMERCIAL_PROJECT":     1.6,
    "MINIFLEX_OPPORTUNITY":   1.8,  # highest weight — directly relevant
    "INFRASTRUCTURE":         1.5,
    "ANNEXATION":             1.3,
    "GENERAL_PLAN_AMENDMENT": 1.2,
    "LARGE_PROJECT":          1.3,
    "DEVELOPER_ACTIVITY":     0.8,
}

# Status multipliers
STATUS_MULTIPLIERS = {
    "APPROVED":   1.0,
    "PROPOSED":   0.7,
    "TABLED":     0.5,
    "CONTINUED":  0.5,
    "DENIED":     0.1,
}

def score_signal(signal: dict) -> float:
    """Compute a weighted score for a single signal."""
    base        = signal.get("growth_score", 50)
    type_weight = SIGNAL_WEIGHTS.get(signal.get("signal_type", ""), 1.0)
    status_mult = STATUS_MULTIPLIERS.get(signal.get("status", "PROPOSED"), 0.7)
    return base * type_weight * status_mult

def aggregate_signals(parsed_docs: list[dict]) -> dict:
    """
    Aggregate all signals by city and signal type.
    Returns a dict keyed by city with growth scores and signal breakdowns.
    """
    city_data = defaultdict(lambda: {
        "signals":        [],
        "signal_counts":  defaultdict(int),
        "raw_score":      0.0,
        "top_areas":      set(),
        "developers":     set(),
        "recent_dates":   [],
    })

    for doc in parsed_docs:
        source  = doc.get("source", {})
        city    = source.get("city", "Unknown")
        county  = source.get("county", "Unknown")
        date    = source.get("event_date", "")

        city_data[city]["county"]  = county
        city_data[city]["recent_dates"].append(date)

        # Collect top areas from document summary
        for area in doc.get("top_areas", []):
            city_data[city]["top_areas"].add(area)

        for signal in doc.get("signals", []):
            scored = score_signal(signal)
            city_data[city]["raw_score"] += scored
            city_data[city]["signal_counts"][signal["signal_type"]] += 1
            city_data[city]["signals"].append({
                **signal,
                "weighted_score": round(scored, 1),
                "source_date":    date,
                "source_title":   source.get("title", ""),
                "notice_url":     source.get("notice_url", ""),
            })

            if signal.get("developer"):
                city_data[city]["developers"].add(signal["developer"])

    # Normalize scores and build final output
    all_scores = [d["raw_score"] for d in city_data.values() if d["raw_score"] > 0]
    max_score  = max(all_scores) if all_scores else 1

    results = {}
    for city, data in city_data.items():
        normalized    = round((data["raw_score"] / max_score) * 100, 1)
        signal_counts = dict(data["signal_counts"])
        total_signals = sum(signal_counts.values())

        # Grade
        if normalized >= 75:
            grade = "A"
        elif normalized >= 50:
            grade = "B"
        elif normalized >= 25:
            grade = "C"
        else:
            grade = "D"

        # Most recent activity date
        dates = sorted([d for d in data["recent_dates"] if d], reverse=True)
        most_recent = dates[0] if dates else "Unknown"

        results[city] = {
            "city":           city,
            "county":         data.get("county", "Unknown"),
            "growth_score":   normalized,
            "grade":          grade,
            "total_signals":  total_signals,
            "signal_counts":  signal_counts,
            "top_areas":      sorted(list(data["top_areas"]))[:10],
            "active_developers": sorted(list(data["developers"]))[:10],
            "most_recent_activity": most_recent,
            "signals":        sorted(data["signals"],
                                     key=lambda s: s.get("weighted_score", 0),
                                     reverse=True)[:20],  # top 20 signals per city
        }

    return results

def build_miniflex_targets(city_scores: dict) -> list[dict]:
    """
    Filter and rank cities/areas specifically for mini-flex opportunity.
    Returns ranked list of targets with justification.
    """
    targets = []
    for city, data in city_scores.items():
        # Count mini-flex specific signals
        miniflex_signals = [
            s for s in data["signals"]
            if s.get("signal_type") in ("MINIFLEX_OPPORTUNITY", "COMMERCIAL_PROJECT",
                                         "REZONE", "INFRASTRUCTURE")
        ]
        # Count residential growth (rooftop density signals)
        residential_signals = [
            s for s in data["signals"]
            if s.get("signal_type") == "NEW_SUBDIVISION"
        ]

        miniflex_score = (
            data["growth_score"] * 0.5 +
            len(miniflex_signals) * 8 +
            len(residential_signals) * 5
        )

        if miniflex_score > 10:
            targets.append({
                "city":                city,
                "county":              data["county"],
                "overall_growth_grade": data["grade"],
                "miniflex_score":      round(miniflex_score, 1),
                "miniflex_signals":    len(miniflex_signals),
                "residential_growth":  len(residential_signals),
                "top_areas":           data["top_areas"],
                "active_developers":   data["active_developers"],
                "most_recent_activity": data["most_recent_activity"],
                "key_signals":         miniflex_signals[:5],
            })

    return sorted(targets, key=lambda t: t["miniflex_score"], reverse=True)

def save_aggregated_output(city_scores: dict, miniflex_targets: list[dict]):
    """Save aggregated results to JSON files."""
    out_dir = Path(JSON_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Full city scores
    city_path = out_dir / "city_growth_scores.json"
    with open(city_path, "w") as f:
        json.dump(city_scores, f, indent=2)
    log.info(f"City scores saved: {city_path}")

    # Mini-flex targets
    mf_path = out_dir / "miniflex_targets.json"
    with open(mf_path, "w") as f:
        json.dump(miniflex_targets, f, indent=2)
    log.info(f"Mini-flex targets saved: {mf_path}")

    # Summary report
    summary = {
        "generated_at":    datetime.now().isoformat(),
        "cities_analyzed": len(city_scores),
        "top_growth_cities": [
            {"city": c, "grade": d["grade"], "score": d["growth_score"]}
            for c, d in sorted(city_scores.items(),
                               key=lambda x: x[1]["growth_score"], reverse=True)[:10]
        ],
        "top_miniflex_targets": [
            {"city": t["city"], "score": t["miniflex_score"], "top_areas": t["top_areas"][:3]}
            for t in miniflex_targets[:5]
        ],
    }
    summary_path = out_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info(f"Summary saved: {summary_path}")

    return city_path, mf_path, summary_path


if __name__ == "__main__":
    # Load all cached parsed docs and aggregate
    import glob
    doc_files = glob.glob(f"{JSON_DIR}/*.json")
    parsed_docs = []
    for f in doc_files:
        if "city_growth" in f or "miniflex" in f or "summary" in f:
            continue
        with open(f) as fh:
            parsed_docs.append(json.load(fh))

    if parsed_docs:
        scores  = aggregate_signals(parsed_docs)
        targets = build_miniflex_targets(scores)
        save_aggregated_output(scores, targets)
        print(f"Aggregated {len(parsed_docs)} docs → {len(scores)} cities")
    else:
        print("No parsed JSON files found. Run parser.py first.")
