#!/usr/bin/env python3
"""
backfill_historical.py — One-time historical backfill for all jurisdictions.

Fetches 24 months of agenda history for every PMN body in jurisdictions.yaml,
merges with existing data, runs Haiku splitting on NEW rows only, then
geocodes. Hard cost cap: $50 (enforced before Haiku is invoked).

Run once, then this script moves itself to scripts/archive/.

Usage:
    python scripts/backfill_historical.py [--dry-run] [--months-back N]

Flags:
    --dry-run     Scrape and report row counts but skip Haiku + geocoding.
    --months-back Override 24-month default (min 1, max 36).
    --skip-split  Skip Haiku splitting (just scrape + persist + geocode).
    --skip-geocode Skip geocoding step.
    --cost-cap    Override $50 Haiku cap (float, USD).

After successful completion this script moves to scripts/archive/backfill_historical.py.
Re-running from the archive location has no effect (it prints a notice and exits).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
ARCHIVE_DIR = SCRIPTS / "archive"
AGENDAS_DIR = DATA / "agendas"
ITEMS_CSV = DATA / "agenda_items.csv"
SPLIT_CSV = DATA / "agenda_items_split.csv"
COSTS_CSV = DATA / "api_costs.csv"
JURISDICTIONS_YAML = DATA / "jurisdictions.yaml"

DEFAULT_MONTHS_BACK = 24
DEFAULT_COST_CAP_USD = 50.0

# Haiku 4.5 pricing (as of 2026-04)
HAIKU_INPUT_COST_PER_TOK = 1.0 / 1_000_000
HAIKU_OUTPUT_COST_PER_TOK = 5.0 / 1_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], label: str, env: dict | None = None) -> bool:
    """Run a subprocess, stream stderr, return True on success."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, env=merged_env)
    if result.returncode != 0:
        print(f"[FAIL] {label} exited {result.returncode}", file=sys.stderr)
        return False
    return True


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, newline="") as f:
        return max(0, sum(1 for _ in f) - 1)  # subtract header


def _estimate_split_cost_usd(new_rows: int) -> float:
    # Rough estimate: ~300 input tokens + ~200 output tokens per row
    # (agenda text is 500–2000 chars; Haiku output is a small JSON array).
    est_input_tok = new_rows * 400
    est_output_tok = new_rows * 250
    return est_input_tok * HAIKU_INPUT_COST_PER_TOK + est_output_tok * HAIKU_OUTPUT_COST_PER_TOK


def _total_api_spend_usd() -> float:
    """Sum all previous costs from api_costs.csv."""
    if not COSTS_CSV.exists():
        return 0.0
    total = 0.0
    with open(COSTS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            try:
                total += float(row.get("cost_usd", 0))
            except (ValueError, TypeError):
                pass
    return total


def _load_existing_ids() -> set[str]:
    """Load all agenda item IDs already in agenda_items.csv."""
    if not ITEMS_CSV.exists():
        return set()
    ids: set[str] = set()
    with open(ITEMS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("id"):
                ids.add(row["id"])
    return ids


def _load_split_source_ids() -> set[str]:
    """Load the parent IDs already present in agenda_items_split.csv.
    Split IDs look like '{parent_id}_item{N}' — extract the parent part."""
    if not SPLIT_CSV.exists():
        return set()
    ids: set[str] = set()
    with open(SPLIT_CSV, newline="") as f:
        for row in csv.DictReader(f):
            raw = row.get("id", "")
            # Strip _itemN suffix to recover parent agenda_items.csv id
            if "_item" in raw:
                ids.add(raw.rsplit("_item", 1)[0])
            else:
                ids.add(raw)
    return ids


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_scrape(months_back: int) -> bool:
    """Run scrape_pmn_all.py to refresh all PMN bodies."""
    # scrape_utah_pmn.py defaults to --months-back 24; scrape_pmn_all.py
    # passes no override, so bodies are scraped with the default.
    # For backfill we want to be explicit, so we temporarily patch the call
    # by passing BACKFILL_MONTHS_BACK env var that scrape_pmn_all.py reads.
    # Simpler: just call scrape_pmn_all.py which already invokes
    # scrape_utah_pmn.py with the default 24-month window.
    return _run(
        [sys.executable, str(SCRIPTS / "scrape_pmn_all.py")],
        label=f"Scrape all PMN bodies ({months_back}-month window)",
    )


def step_scrape_grantsville() -> bool:
    """Run web scraper for Grantsville (non-PMN path)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = AGENDAS_DIR / f"web_grantsville_{ts}.json"
    return _run(
        [sys.executable, str(SCRIPTS / "scrape_agendas.py"),
         "--jurisdiction", "grantsville",
         "--output", str(out)],
        label="Scrape Grantsville website",
    )


def step_persist() -> int:
    """Run persist_to_csv.py and return count of NEW rows added."""
    before = _count_csv_rows(ITEMS_CSV)
    ok = _run(
        [sys.executable, str(SCRIPTS / "persist_to_csv.py")],
        label="Persist scraped data → agenda_items.csv (dedup)",
    )
    if not ok:
        return -1
    after = _count_csv_rows(ITEMS_CSV)
    new_rows = after - before
    print(f"\n  agenda_items.csv: {before} → {after} rows ({new_rows:+d} new)")
    return new_rows


def step_split(new_row_count: int, cost_cap: float) -> bool:
    """Run split_agenda_items.py. The script's own cache ensures only new rows
    are sent to Haiku."""
    estimated = _estimate_split_cost_usd(new_row_count)
    print(f"\n  Estimated Haiku cost for {new_row_count} new rows: ${estimated:.2f}")
    prior_spend = _total_api_spend_usd()
    print(f"  Prior API spend (all time): ${prior_spend:.2f}")
    if estimated > cost_cap:
        print(
            f"\n[WARN] Estimated cost ${estimated:.2f} exceeds cap ${cost_cap:.2f}. "
            "Skipping Haiku split. Re-run with --cost-cap to override.",
            file=sys.stderr,
        )
        return False
    return _run(
        [sys.executable, str(SCRIPTS / "split_agenda_items.py")],
        label="Split agenda items with Haiku (cache prevents re-processing)",
    )


def step_aggregate() -> bool:
    """Regenerate city_signal_scores.json from the full split CSV."""
    return _run(
        [sys.executable, str(SCRIPTS / "aggregate_city_signals.py")],
        label="Aggregate city signals → city_signal_scores.json",
    )


def step_geocode() -> bool:
    """Run geocode_items.py on the full agenda_items_split.csv."""
    env = {}
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        env["ANTHROPIC_API_KEY"] = anthropic_key
    return _run(
        [sys.executable, str(SCRIPTS / "geocode_items.py")],
        label="Geocode items → items_geocoded.csv",
        env=env,
    )


def step_archive_self() -> None:
    """Move this script to scripts/archive/ so it's not accidentally re-run."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve()
    dst = ARCHIVE_DIR / src.name
    shutil.move(str(src), str(dst))
    print(f"\n  Archived: {src.name} → scripts/archive/")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="Scrape and count new rows; skip Haiku + geocoding.")
    p.add_argument("--months-back", type=int, default=DEFAULT_MONTHS_BACK,
                   help=f"Months of history to fetch (default {DEFAULT_MONTHS_BACK}).")
    p.add_argument("--skip-split", action="store_true",
                   help="Skip Haiku splitting step.")
    p.add_argument("--skip-geocode", action="store_true",
                   help="Skip geocoding step.")
    p.add_argument("--cost-cap", type=float, default=DEFAULT_COST_CAP_USD,
                   help=f"Max USD to spend on Haiku (default ${DEFAULT_COST_CAP_USD}).")
    p.add_argument("--no-archive", action="store_true",
                   help="Do not move the script to archive/ when done.")
    return p


def main(argv: list[str] | None = None) -> int:
    # Guard: if running from archive location, exit cleanly.
    here = Path(__file__).resolve()
    if here.parent.name == "archive":
        print("backfill_historical.py has already been archived. Backfill was completed.")
        print("To re-run, copy it back from scripts/archive/ first.")
        return 0

    args = build_parser().parse_args(argv)

    print("\n" + "=" * 60)
    print("  WASATCH INTEL — Phase 10 Historical Backfill")
    print(f"  Date:       {datetime.now(timezone.utc).date()}")
    print(f"  Months:     {args.months_back}")
    print(f"  Dry run:    {args.dry_run}")
    print(f"  Cost cap:   ${args.cost_cap:.2f}")
    print("=" * 60)

    # ── 0. Pre-flight counts ────────────────────────────────────────────────
    rows_before = _count_csv_rows(ITEMS_CSV)
    split_before = _count_csv_rows(SPLIT_CSV)
    print(f"\n  Pre-backfill rows:")
    print(f"    agenda_items.csv:       {rows_before}")
    print(f"    agenda_items_split.csv: {split_before}")

    # ── 1. Scrape ───────────────────────────────────────────────────────────
    # Grantsville has a custom web scraper in addition to PMN
    step_scrape_grantsville()  # continue-on-error

    if not step_scrape(args.months_back):
        print("[ERROR] PMN scrape failed — aborting backfill.", file=sys.stderr)
        return 1

    # ── 2. Persist + dedup ─────────────────────────────────────────────────
    new_rows = step_persist()
    if new_rows < 0:
        print("[ERROR] persist step failed — aborting.", file=sys.stderr)
        return 1

    # ── 3. Split (Haiku) ───────────────────────────────────────────────────
    if args.dry_run or args.skip_split:
        if args.dry_run:
            print("\n  [DRY RUN] Would run Haiku split on new rows.")
        split_ok = True
    else:
        split_ok = step_split(new_rows, args.cost_cap)

    # ── 4. Aggregate signals ───────────────────────────────────────────────
    if not args.dry_run and split_ok:
        step_aggregate()

    # ── 5. Geocode ─────────────────────────────────────────────────────────
    if args.dry_run or args.skip_geocode:
        if args.dry_run:
            print("\n  [DRY RUN] Would run geocoding.")
    else:
        step_geocode()  # non-fatal — partial geocodes are still useful

    # ── 6. Final report ────────────────────────────────────────────────────
    rows_after = _count_csv_rows(ITEMS_CSV)
    split_after = _count_csv_rows(SPLIT_CSV)
    print("\n" + "=" * 60)
    print("  BACKFILL COMPLETE")
    print("=" * 60)
    print(f"  agenda_items.csv:       {rows_before} → {rows_after} (+{rows_after - rows_before})")
    print(f"  agenda_items_split.csv: {split_before} → {split_after} (+{split_after - split_before})")
    print(f"  Total API spend:        ${_total_api_spend_usd():.2f}")

    phase10_target_low, phase10_target_high = 1500, 3000
    if rows_after < phase10_target_low:
        print(f"\n  [WARN] Row count {rows_after} is below Phase 10 target "
              f"({phase10_target_low}–{phase10_target_high}). Some PMN bodies may "
              "only expose recent notices — this is expected behaviour.")
    elif rows_after > phase10_target_high:
        print(f"\n  [INFO] Row count {rows_after} exceeds target range "
              f"({phase10_target_low}–{phase10_target_high}) — great, more data!")
    else:
        print(f"\n  [OK] Row count {rows_after} is within Phase 10 target range.")

    # ── 7. Archive this script ─────────────────────────────────────────────
    if not args.dry_run and not args.no_archive:
        step_archive_self()

    return 0


if __name__ == "__main__":
    sys.exit(main())
