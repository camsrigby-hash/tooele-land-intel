#!/usr/bin/env python3
"""Split bundled agenda PDFs into individual items using Claude Haiku.

Reads: data/agenda_items.csv
Writes: data/agenda_items_split.csv (one row per sub-item)
Also logs: data/api_costs.csv
"""
import csv, json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone
import anthropic

ROOT = Path(__file__).parent.parent
IN_CSV = ROOT / "data" / "agenda_items.csv"
OUT_CSV = ROOT / "data" / "agenda_items_split.csv"
COST_CSV = ROOT / "data" / "api_costs.csv"
CACHE = ROOT / "data" / ".split_cache.json"

MODEL = "claude-haiku-4-5-20251001"
# Haiku 4.5 pricing: $1/M input, $5/M output
COST_PER_INPUT_TOK = 1.0 / 1_000_000
COST_PER_OUTPUT_TOK = 5.0 / 1_000_000

client = anthropic.Anthropic()

PROMPT = """You are extracting individual agenda items from a municipal meeting agenda or packet. The text below is one meeting's content.

Return a JSON array where each element represents ONE discrete agenda item (a rezone request, subdivision review, resolution, conditional use permit, ordinance, public hearing, etc.). Skip procedural items like "Roll Call", "Pledge of Allegiance", "Approval of Minutes", "Public Comment" unless they reference a specific development.

For each item, return:
- "title": one-sentence description (include project name, address, or parcel if mentioned)
- "item_type": one of: rezone, residential_subdivision, residential_density, commercial, industrial, mixed_use, annexation, general_plan_amendment, conditional_use, site_plan, ordinance, resolution, public_hearing, other
- "parcel_ids": array of any parcel IDs mentioned (format XX-XXX-X-XXXX), else []
- "addresses": array of street addresses mentioned, else []
- "applicant": person or entity name if identified, else null
- "acres": numeric acreage if mentioned, else null

Return ONLY the JSON array, no other text. If no substantive items exist, return [].

Meeting text:
"""


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
        "script": script_name,
        "model": MODEL,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 6),
        "items": items_processed,
    }
    is_new = not COST_CSV.exists()
    with open(COST_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)
    return cost


def split_one(text, cache):
    key = str(hash(text[:500] + str(len(text))))
    if key in cache:
        return cache[key], 0, 0
    resp = client.messages.create(
        model=MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT + text[:8000]}],
    )
    raw = resp.content[0].text.strip()
    # Strip possible code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        items = json.loads(raw.strip())
    except json.JSONDecodeError:
        items = []
    cache[key] = items
    save_cache(cache)
    return items, resp.usage.input_tokens, resp.usage.output_tokens


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
            # Too short to split — keep as-is
            out_rows.append(row.to_dict())
            continue
        print(f"[{i+1}/{len(df)}] {row.get('jurisdiction')} — {str(row.get('title'))[:60]}", file=sys.stderr)
        try:
            items, in_t, out_t = split_one(text, cache)
            total_in += in_t
            total_out += out_t
            processed += 1
        except Exception as e:
            print(f"  [ERROR] {e}", file=sys.stderr)
            out_rows.append(row.to_dict())
            continue

        if not items:
            # No sub-items detected — keep parent row
            out_rows.append(row.to_dict())
            continue

        # Emit one row per sub-item, inheriting parent fields
        for idx, it in enumerate(items):
            new = row.to_dict()
            new["id"] = f"{row['id']}_item{idx}"
            new["title"] = it.get("title", row["title"])
            new["item_type"] = it.get("item_type") or row.get("item_type", "")
            new["confidence"] = 0.9  # LLM-extracted = high confidence
            # Store structured fields as JSON in agenda_text slot
            extras = {k: it.get(k) for k in ("parcel_ids", "addresses", "applicant", "acres") if it.get(k)}
            new["agenda_text"] = json.dumps(extras) if extras else ""
            out_rows.append(new)
        time.sleep(0.1)  # gentle rate limit

    # Write output
    if out_rows:
        fields = list(out_rows[0].keys())
        with open(OUT_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(out_rows)

    cost = log_cost("split_agenda_items", total_in, total_out, processed)
    print(f"\n✓ Processed {processed} agendas, wrote {len(out_rows)} rows to {OUT_CSV.name}", file=sys.stderr)
    print(f"✓ Cost: ${cost:.4f} ({total_in:,} input + {total_out:,} output tokens)", file=sys.stderr)


if __name__ == "__main__":
    main()
