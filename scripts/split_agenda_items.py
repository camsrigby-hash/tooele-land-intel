#!/usr/bin/env python3
"""Split bundled agenda PDFs into individual items using Claude Haiku.

Reads: data/agenda_items.csv
Writes: data/agenda_items_split.csv (one row per sub-item, CM_RE signal schema)
Also logs: data/api_costs.csv

Schema upgraded in Phase 1 (2026-04-23) to use the richer CM_RE signal
taxonomy. New columns: signal_type, description, location, units, developer,
zoning_from, zoning_to, status_enum, growth_score, notes.
"""
import csv, json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone
import anthropic

ROOT     = Path(__file__).parent.parent
IN_CSV   = ROOT / "data" / "agenda_items.csv"
OUT_CSV  = ROOT / "data" / "agenda_items_split.csv"
COST_CSV = ROOT / "data" / "api_costs.csv"
CACHE    = ROOT / "data" / ".split_cache.json"

MODEL = "claude-haiku-4-5-20251001"
COST_PER_INPUT_TOK  = 1.0 / 1_000_000
COST_PER_OUTPUT_TOK = 5.0 / 1_000_000

# CM_RE signal taxonomy — shared with wasatch-intel/src/lib/types.ts
VALID_SIGNAL_TYPES = {
    "REZONE", "NEW_SUBDIVISION", "COMMERCIAL_PROJECT", "MINIFLEX_OPPORTUNITY",
    "INFRASTRUCTURE", "ANNEXATION", "GENERAL_PLAN_AMENDMENT", "LARGE_PROJECT",
    "DEVELOPER_ACTIVITY",
}
VALID_STATUSES = {"PROPOSED", "APPROVED", "DENIED", "TABLED", "CONTINUED"}

# Keyword fallback: old item_type -> signal_type for pre-Phase-1 rows
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

client = anthropic.Anthropic()

# CM_RE PROMPT_TEMPLATE adapted for Anthropic API (not claude CLI).
# Source: vendor/cm_re/scraper/parser.py PROMPT_TEMPLATE — adapted for per-item
# extraction (not per-document), using Haiku 4.5, Anthropic SDK.
PROMPT = (
    "You are a Utah real estate development intelligence analyst.\n"
    "Read this city planning or city council meeting agenda/packet text and extract "
    "individual agenda items.\n\n"
    "Respond ONLY with a valid JSON array - no markdown fences, no preamble.\n\n"
    "For each substantive agenda item (skip procedural items: Roll Call, Pledge of "
    "Allegiance, Approval of Minutes, adjournment):\n\n"
    "Return objects with these fields:\n"
    '{\n'
    '  "title": "one-sentence description (include project name, address, or parcel if mentioned)",\n'
    '  "signal_type": "REZONE | NEW_SUBDIVISION | COMMERCIAL_PROJECT | MINIFLEX_OPPORTUNITY | INFRASTRUCTURE | ANNEXATION | GENERAL_PLAN_AMENDMENT | LARGE_PROJECT | DEVELOPER_ACTIVITY | null",\n'
    '  "description": "plain English description of what is happening",\n'
    '  "location": "address or cross streets as stated in document, or null",\n'
    '  "parcel_ids": ["XX-XXX-X-XXXX parcel IDs"] or [],\n'
    '  "developer": "applicant or developer entity name, or null",\n'
    '  "acres": null or number,\n'
    '  "units": null or number,\n'
    '  "zoning_from": null or "existing zone code",\n'
    '  "zoning_to": null or "proposed zone code",\n'
    '  "status": "PROPOSED | APPROVED | DENIED | TABLED | CONTINUED",\n'
    '  "growth_score": 0-100,\n'
    '  "notes": "context useful to a land developer, or null"\n'
    "}\n\n"
    "Signal types:\n"
    "- REZONE: zone change (commercial/industrial/mixed-use rezones are highest value)\n"
    "- NEW_SUBDIVISION: residential plats, preliminary/final plat approvals\n"
    "- COMMERCIAL_PROJECT: commercial, retail, office, industrial proposals\n"
    "- MINIFLEX_OPPORTUNITY: light industrial, flex space, storage, contractor condo\n"
    "- INFRASTRUCTURE: roads, intersections, utilities (require action verbs - not addresses)\n"
    "- ANNEXATION: land being brought into city limits\n"
    "- GENERAL_PLAN_AMENDMENT: future land use map or general plan changes\n"
    "- LARGE_PROJECT: development 50+ units or 5+ acres\n"
    "- DEVELOPER_ACTIVITY: named developer appearing, no other category fits\n\n"
    "Return [] if no substantive items found.\n\n"
    "Meeting text:\n"
)

OUT_FIELDS = [
    "id", "jurisdiction", "body", "meeting_date", "title",
    "item_type", "confidence",
    "signal_type", "description", "location",
    "acres", "units", "developer", "zoning_from", "zoning_to",
    "status_enum", "growth_score", "notes",
    "url", "agenda_text", "source", "scraped_at",
]


def load_cache():
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def save_cache(c):
    CACHE.write_text(json.dumps(c))


def log_cost(script_name, in_tok, out_tok, items_processed):
    cost = in_tok * COST_PER_INPUT_TOK + out_tok * COST_PER_OUTPUT_TOK
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": script_name, "model": MODEL,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cost_usd": round(cost, 6), "items": items_processed,
    }
    is_new = not COST_CSV.exists()
    with open(COST_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)
    return cost


def sanitize_signal(v):
    return v if v in VALID_SIGNAL_TYPES else ""


def sanitize_status(v):
    return v if v in VALID_STATUSES else "PROPOSED"


def split_one(text, cache):
    key = str(hash(text[:500] + str(len(text))))
    if key in cache:
        return cache[key], 0, 0
    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        messages=[{"role": "user", "content": PROMPT + text[:8000]}],
    )
    raw = resp.content[0].text.strip()
    # Strip markdown fences if Haiku adds them
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("["):
                raw = part
                break
    # Find JSON array if prefixed with text
    if not raw.startswith("["):
        start = raw.find("[")
        if start >= 0:
            raw = raw[start:]
    try:
        items = json.loads(raw.strip())
    except json.JSONDecodeError:
        items = []
    cache[key] = items
    save_cache(cache)
    return items, resp.usage.input_tokens, resp.usage.output_tokens


def _blank_new_fields(d):
    d.setdefault("signal_type", ITEM_TYPE_TO_SIGNAL.get(str(d.get("item_type", "")), ""))
    for col in ("description", "location", "developer", "zoning_from", "zoning_to", "notes", "growth_score"):
        d.setdefault(col, "")
    d.setdefault("status_enum", "PROPOSED")
    d.setdefault("units", "")
    return d


def main():
    import pandas as pd
    df = pd.read_csv(IN_CSV)
    cache = load_cache()
    out_rows = []
    total_in = total_out = 0
    processed = 0

    for i, row in df.iterrows():
        text = str(row.get("agenda_text") or "")
        if len(text) < 100:
            out_rows.append(_blank_new_fields(row.to_dict()))
            continue

        print(f"[{i+1}/{len(df)}] {row.get('jurisdiction')} - {str(row.get('title'))[:60]}", file=sys.stderr)
        try:
            items, in_t, out_t = split_one(text, cache)
            total_in += in_t
            total_out += out_t
            processed += 1
        except Exception as e:
            print(f"  [ERROR] {e}", file=sys.stderr)
            out_rows.append(_blank_new_fields(row.to_dict()))
            continue

        if not items:
            out_rows.append(_blank_new_fields(row.to_dict()))
            continue

        for idx, it in enumerate(items):
            new = row.to_dict()
            new["id"]          = f"{row['id']}_item{idx}"
            new["title"]       = it.get("title") or row["title"]
            new["signal_type"] = sanitize_signal(it.get("signal_type", ""))
            if not new.get("item_type"):
                new["item_type"] = new["signal_type"].lower() if new["signal_type"] else "other"
            new["confidence"]  = 0.9
            new["description"] = it.get("description", "")
            new["location"]    = it.get("location", "")
            new["developer"]   = it.get("developer", "")
            new["acres"]       = it.get("acres", "")
            new["units"]       = it.get("units", "")
            new["zoning_from"] = it.get("zoning_from", "")
            new["zoning_to"]   = it.get("zoning_to", "")
            new["status_enum"] = sanitize_status(it.get("status", ""))
            new["growth_score"]= it.get("growth_score", "")
            new["notes"]       = it.get("notes", "")
            pids = it.get("parcel_ids", [])
            new["agenda_text"] = json.dumps({"parcel_ids": pids}) if pids else ""
            out_rows.append(new)

        time.sleep(0.1)

    if out_rows:
        with open(OUT_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(out_rows)

    cost = log_cost("split_agenda_items", total_in, total_out, processed)
    print(f"\nProcessed {processed} agendas, wrote {len(out_rows)} rows", file=sys.stderr)
    print(f"Cost: ${cost:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
