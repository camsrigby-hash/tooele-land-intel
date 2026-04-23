#!/usr/bin/env python3
"""Generate a weekly market-intelligence digest from recent agenda items.

Reads: data/agenda_items_split.csv (preferred) or data/agenda_items.csv
Writes: data/digests/YYYY-MM-DD.md  + updates data/latest_digest.md
"""
import csv, json, os, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import anthropic
import pandas as pd

ROOT = Path(__file__).parent.parent
SPLIT_CSV = ROOT / "data" / "agenda_items_split.csv"
FALLBACK_CSV = ROOT / "data" / "agenda_items.csv"
COST_CSV = ROOT / "data" / "api_costs.csv"
DIGEST_DIR = ROOT / "data" / "digests"
LATEST = ROOT / "data" / "latest_digest.md"

MODEL = "claude-opus-4-7"
# Opus 4.7 pricing: $15/M input, $75/M output
COST_PER_INPUT_TOK = 15.0 / 1_000_000
COST_PER_OUTPUT_TOK = 75.0 / 1_000_000

DAYS_WINDOW = 14  # trailing window for "recent" items

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a senior land-development market analyst covering the Wasatch Front and Tooele Valley in Utah. You write weekly intelligence digests for a commercial real estate professional. Your tone is direct, specific, and skeptical. You surface signals, not news.

For each weekly digest, you produce a markdown document with these sections:

## This Week's Signal
A 2-3 sentence lead. What's the single most important thing that happened this period, and why should the reader care. Name specific projects, acreages, or applicants. No hedging.

## What Moved
Bullet list of 4-8 substantive items from the period. For each: project name, jurisdiction, acreage if known, item type (rezone, subdivision, etc.), and the ONE thing that makes it worth noting. Skip routine business (minutes, bills, appointments).

## Pattern Watch
1-3 observations about trends across the items. Are multiple rezones clustering geographically? Is one applicant appearing repeatedly? Is there a corridor (SR-36, SR-138, Midvalley) seeing unusual activity? Only include this section if you see a real pattern; say so honestly if you don't.

## Diligence Queue
3-5 specific follow-up questions a real estate professional should chase this week. Be concrete: "Call Grantsville zoning re: Mack Canyon setback variance" not "follow up on land use matters."

Skip any section that has no content. Never invent facts. If an item's details are unclear, say so."""


def log_cost(script_name, in_tok, out_tok, items):
    cost = in_tok * COST_PER_INPUT_TOK + out_tok * COST_PER_OUTPUT_TOK
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": script_name, "model": MODEL,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cost_usd": round(cost, 6), "items": items,
    }
    is_new = not COST_CSV.exists()
    with open(COST_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new: w.writeheader()
        w.writerow(row)
    return cost


def main():
    csv_path = SPLIT_CSV if SPLIT_CSV.exists() else FALLBACK_CSV
    df = pd.read_csv(csv_path)
    df["meeting_date"] = pd.to_datetime(df["meeting_date"], errors="coerce")

    cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=DAYS_WINDOW)
    recent = df[df["meeting_date"] >= cutoff].copy()

    if recent.empty:
        print("No recent items — writing empty digest", file=sys.stderr)
        content = f"# Weekly Digest — {datetime.now().strftime('%b %d, %Y')}\n\nNo substantive agenda activity in the last {DAYS_WINDOW} days."
    else:
        # Build compact item list for the prompt
        items_text = ""
        for _, r in recent.iterrows():
            d = r["meeting_date"].strftime("%Y-%m-%d") if pd.notna(r["meeting_date"]) else "—"
            items_text += f"- [{d}] {r.get('jurisdiction','')} / {r.get('body','')}\n"
            items_text += f"  Title: {r.get('title','')}\n"
            if r.get("item_type"): items_text += f"  Type: {r.get('item_type')}\n"
            extras = r.get("agenda_text", "")
            if extras and str(extras).startswith("{"):
                items_text += f"  Details: {extras}\n"
            elif extras:
                items_text += f"  Excerpt: {str(extras)[:300]}\n"
            items_text += "\n"

        user_msg = (
            f"Here are the {len(recent)} agenda items from the past {DAYS_WINDOW} days "
            f"across Erda and Grantsville, Utah:\n\n{items_text}\n\n"
            "Generate the weekly digest."
        )

        resp = client.messages.create(
            model=MODEL, max_tokens=2500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        body = resp.content[0].text.strip()
        cost = log_cost("weekly_digest", resp.usage.input_tokens, resp.usage.output_tokens, len(recent))
        today = datetime.now().strftime("%Y-%m-%d")
        content = f"# Weekly Digest — {datetime.now().strftime('%b %d, %Y')}\n\n"
        content += f"*{len(recent)} items · trailing {DAYS_WINDOW} days · Claude Opus · ${cost:.3f}*\n\n"
        content += "---\n\n" + body

    # Write dated + latest
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    (DIGEST_DIR / f"{today}.md").write_text(content)
    LATEST.write_text(content)
    print(f"✓ Wrote digest to {LATEST}", file=sys.stderr)


if __name__ == "__main__":
    main()
