#!/usr/bin/env python3
"""Correlate external signals (Reddit, news) to agenda items using Haiku.

The correlation uses the shared CM_RE signal taxonomy so that a Reddit post
tagged COMMERCIAL_PROJECT can match an agenda item with the same signal_type
in the same jurisdiction — much stronger than keyword-only matching.

Scoring axes (from PROMPT_PLAYBOOK_ADDENDUM.md Phase 6):
    jurisdiction_match  weight 0.4
    signal_type_match   weight 0.3
    keyword_overlap     weight 0.2
    temporal_proximity  weight 0.1

Only matches with total_score >= CORR_THRESHOLD (default 0.6) are written.

Inputs:
    data/signals_reddit.csv   — from scrape_reddit.py (may be empty)
    data/signals_news.csv     — from scrape_news_rss.py
    data/agenda_items_split.csv (or items_geocoded.csv if present)

Outputs:
    data/signal_correlations.csv
    Appends to data/api_costs.csv

Cost cap: 200 Haiku calls per run (~$0.20).

Usage:
    python scripts/correlate_signals.py
    python scripts/correlate_signals.py --days 30 --threshold 0.6
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic

ROOT           = Path(__file__).parent.parent
REDDIT_CSV     = ROOT / "data" / "signals_reddit.csv"
NEWS_CSV       = ROOT / "data" / "signals_news.csv"
AGENDA_GEOCOD  = ROOT / "data" / "items_geocoded.csv"
AGENDA_CSV     = ROOT / "data" / "agenda_items_split.csv"
OUT_CSV        = ROOT / "data" / "signal_correlations.csv"
COST_CSV       = ROOT / "data" / "api_costs.csv"

MODEL               = "claude-haiku-4-5-20251001"
COST_PER_INPUT_TOK  = 1.0 / 1_000_000
COST_PER_OUTPUT_TOK = 5.0 / 1_000_000
HAIKU_CALL_CAP      = 200

# Shared with wasatch-intel/src/lib/types.ts
VALID_SIGNAL_TYPES = {
    "REZONE", "NEW_SUBDIVISION", "COMMERCIAL_PROJECT", "MINIFLEX_OPPORTUNITY",
    "INFRASTRUCTURE", "ANNEXATION", "GENERAL_PLAN_AMENDMENT", "LARGE_PROJECT",
    "DEVELOPER_ACTIVITY", "UNCATEGORIZED",
}

# Jurisdiction synonyms — maps colloquial/partial names to canonical form
JURISDICTION_ALIASES: dict[str, str] = {
    "erda":         "Erda",
    "grantsville":  "Grantsville",
    "tooele":       "Tooele",
    "stansbury":    "Stansbury Park",
    "lake point":   "Lake Point",
    "saratoga":     "Saratoga Springs",
    "eagle mountain": "Eagle Mountain",
    "lehi":         "Lehi",
    "herriman":     "Herriman",
    "bluffdale":    "Bluffdale",
    "south jordan": "South Jordan",
    "spanish fork": "Spanish Fork",
    "salt lake":    "Salt Lake City",
}

# Correlation axis weights
W_JURISDICTION = 0.4
W_SIGNAL_TYPE  = 0.3
W_KEYWORDS     = 0.2
W_TEMPORAL     = 0.1

CORR_THRESHOLD = float(os.environ.get("CORRELATION_THRESHOLD", "0.6"))

FIELDNAMES = [
    "signal_id", "signal_source", "signal_date", "signal_headline",
    "signal_type_inferred", "signal_jurisdiction_inferred",
    "agenda_id", "agenda_title", "agenda_date", "agenda_jurisdiction",
    "agenda_signal_type",
    "score_jurisdiction", "score_signal_type", "score_keywords",
    "score_temporal", "total_score",
    "correlated_at",
]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_agendas(days: int) -> list[dict]:
    rows = _read_csv(AGENDA_GEOCOD) if AGENDA_GEOCOD.exists() else _read_csv(AGENDA_CSV)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days * 4)).date().isoformat()
    return [r for r in rows if r.get("meeting_date", "") >= cutoff and r.get("id")]


# ── Haiku classification ──────────────────────────────────────────────────────

CLASSIFY_PROMPT = """\
You are a Utah land-development intelligence analyst.

Classify this news/Reddit signal and extract metadata for correlation.

Signal text:
TITLE: {title}
BODY: {body}

Respond ONLY with a JSON object (no markdown fences):
{{
  "signal_type": "REZONE|NEW_SUBDIVISION|COMMERCIAL_PROJECT|MINIFLEX_OPPORTUNITY|INFRASTRUCTURE|ANNEXATION|GENERAL_PLAN_AMENDMENT|LARGE_PROJECT|DEVELOPER_ACTIVITY|UNCATEGORIZED",
  "jurisdiction": "exact city name or null (e.g. Erda, Grantsville, Tooele, etc.)",
  "project_name": "project or developer name mentioned, or null",
  "keywords": ["list", "of", "salient", "land-development", "keywords"]
}}

signal_type rules:
- REZONE: zone change or rezoning mentioned
- NEW_SUBDIVISION: plat, subdivision, residential development
- COMMERCIAL_PROJECT: retail, office, mixed-use, restaurant
- MINIFLEX_OPPORTUNITY: small-format flex / industrial / warehouse < 5 acres
- INFRASTRUCTURE: roads, utilities, water, sewer, transit
- ANNEXATION: city boundary expansion
- GENERAL_PLAN_AMENDMENT: GP update, long-range planning
- LARGE_PROJECT: major project > ~50 acres or $50M
- DEVELOPER_ACTIVITY: developer purchase, assemblage, permit
- UNCATEGORIZED: doesn't fit any above
"""


def classify_signal(client: anthropic.Anthropic, signal: dict) -> dict:
    """Call Haiku to classify a signal. Returns classification dict."""
    title = signal.get("title", "")
    body  = (signal.get("summary") or signal.get("body") or "")[:600]
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(title=title, body=body)}],
        )
        text = msg.content[0].text.strip()
        # Strip markdown fences if Haiku added them
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        if result.get("signal_type") not in VALID_SIGNAL_TYPES:
            result["signal_type"] = "UNCATEGORIZED"
        return result, msg.usage.input_tokens, msg.usage.output_tokens
    except Exception as exc:
        print(f"  Haiku classify error: {exc}", file=sys.stderr)
        return {"signal_type": "UNCATEGORIZED", "jurisdiction": None, "keywords": []}, 0, 0


# ── Correlation scoring ───────────────────────────────────────────────────────

def _jurisdiction_score(sig_jurisdiction: str | None, agenda_jurisdiction: str) -> float:
    if not sig_jurisdiction:
        return 0.0
    # Try alias mapping first
    canonical = JURISDICTION_ALIASES.get(sig_jurisdiction.lower().strip())
    sig_norm  = canonical or sig_jurisdiction.strip()
    if sig_norm.lower() == agenda_jurisdiction.lower():
        return 1.0
    # Partial — sig mentions the city somewhere
    if sig_norm.lower() in agenda_jurisdiction.lower() or agenda_jurisdiction.lower() in sig_norm.lower():
        return 0.5
    return 0.0


def _signal_type_score(sig_type: str, agenda_type: str) -> float:
    if not sig_type or not agenda_type:
        return 0.0
    if sig_type == agenda_type:
        return 1.0
    # Related pairs
    related = {
        frozenset({"REZONE", "GENERAL_PLAN_AMENDMENT"}),
        frozenset({"NEW_SUBDIVISION", "LARGE_PROJECT"}),
        frozenset({"COMMERCIAL_PROJECT", "MINIFLEX_OPPORTUNITY"}),
        frozenset({"DEVELOPER_ACTIVITY", "REZONE"}),
        frozenset({"DEVELOPER_ACTIVITY", "NEW_SUBDIVISION"}),
    }
    if frozenset({sig_type, agenda_type}) in related:
        return 0.5
    return 0.0


def _keyword_score(sig_keywords: list[str], agenda_title: str, agenda_description: str) -> float:
    if not sig_keywords:
        return 0.0
    haystack = (agenda_title + " " + (agenda_description or "")).lower()
    hits = sum(1 for kw in sig_keywords if kw.lower() in haystack)
    return min(1.0, hits / max(1, len(sig_keywords)))


def _temporal_score(sig_date: str, agenda_date: str) -> float:
    try:
        d1 = datetime.fromisoformat(sig_date)
        d2 = datetime.fromisoformat(agenda_date)
        delta_days = abs((d1 - d2).days)
        if delta_days <= 7:
            return 1.0
        if delta_days <= 30:
            return 0.7
        if delta_days <= 90:
            return 0.3
        return 0.0
    except Exception:
        return 0.0


def score_correlation(signal: dict, classification: dict, agenda: dict) -> float:
    s_juris = _jurisdiction_score(classification.get("jurisdiction"), agenda.get("jurisdiction", ""))
    s_type  = _signal_type_score(classification.get("signal_type", "UNCATEGORIZED"), agenda.get("signal_type", ""))
    s_kw    = _keyword_score(
        classification.get("keywords", []),
        agenda.get("title", ""),
        agenda.get("description", ""),
    )
    s_temp  = _temporal_score(signal.get("published_date", ""), agenda.get("meeting_date", ""))
    return round(
        s_juris * W_JURISDICTION + s_type * W_SIGNAL_TYPE + s_kw * W_KEYWORDS + s_temp * W_TEMPORAL,
        3,
    ), s_juris, s_type, s_kw, s_temp


# ── Main ──────────────────────────────────────────────────────────────────────

def main(days: int = 30, threshold: float = CORR_THRESHOLD) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — exiting", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Load signals
    reddit_rows = _read_csv(REDDIT_CSV)
    news_rows   = _read_csv(NEWS_CSV)
    signals     = reddit_rows + news_rows
    print(f"Loaded {len(reddit_rows)} Reddit + {len(news_rows)} news signals", file=sys.stderr)

    # Load agendas (wider look-back — 4× the signal window)
    agendas = _load_agendas(days)
    print(f"Loaded {len(agendas)} agenda items for correlation", file=sys.stderr)

    if not signals or not agendas:
        print("Nothing to correlate — writing empty output", file=sys.stderr)
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return

    total_input_toks  = 0
    total_output_toks = 0
    haiku_calls       = 0
    corr_at           = datetime.now(timezone.utc).isoformat()
    correlations: list[dict] = []

    for signal in signals:
        if haiku_calls >= HAIKU_CALL_CAP:
            print(f"Haiku call cap ({HAIKU_CALL_CAP}) reached — stopping early", file=sys.stderr)
            break

        classification, in_toks, out_toks = classify_signal(client, signal)
        total_input_toks  += in_toks
        total_output_toks += out_toks
        haiku_calls       += 1

        # Score against every candidate agenda item
        for agenda in agendas:
            total, s_j, s_t, s_k, s_tp = score_correlation(signal, classification, agenda)
            if total < threshold:
                continue
            correlations.append({
                "signal_id":                  signal.get("id", ""),
                "signal_source":              signal.get("source", ""),
                "signal_date":                signal.get("published_date", ""),
                "signal_headline":            signal.get("title", "")[:200],
                "signal_type_inferred":       classification.get("signal_type", "UNCATEGORIZED"),
                "signal_jurisdiction_inferred": classification.get("jurisdiction") or "",
                "agenda_id":                  agenda.get("id", ""),
                "agenda_title":               agenda.get("title", "")[:200],
                "agenda_date":                agenda.get("meeting_date", ""),
                "agenda_jurisdiction":        agenda.get("jurisdiction", ""),
                "agenda_signal_type":         agenda.get("signal_type", ""),
                "score_jurisdiction":         s_j,
                "score_signal_type":          s_t,
                "score_keywords":             s_k,
                "score_temporal":             s_tp,
                "total_score":                total,
                "correlated_at":              corr_at,
            })

        time.sleep(0.1)  # avoid Haiku burst

    print(f"Haiku calls: {haiku_calls}  correlations above {threshold}: {len(correlations)}", file=sys.stderr)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(correlations)
    print(f"Written: {OUT_CSV}", file=sys.stderr)

    # Log cost
    total_cost = (total_input_toks * COST_PER_INPUT_TOK) + (total_output_toks * COST_PER_OUTPUT_TOK)
    COST_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not COST_CSV.exists()
    with COST_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["run_date", "script", "model", "items", "input_tokens", "output_tokens", "cost_usd"])
        if write_header:
            w.writeheader()
        w.writerow({
            "run_date":     corr_at[:10],
            "script":       "correlate_signals",
            "model":        MODEL,
            "items":        haiku_calls,
            "input_tokens": total_input_toks,
            "output_tokens": total_output_toks,
            "cost_usd":     round(total_cost, 6),
        })
    print(f"Cost: ${total_cost:.4f}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days",      type=int,   default=30,  help="Look-back window in days")
    parser.add_argument("--threshold", type=float, default=0.6, help="Minimum correlation score (0.0-1.0)")
    args = parser.parse_args()
    main(days=args.days, threshold=args.threshold)
