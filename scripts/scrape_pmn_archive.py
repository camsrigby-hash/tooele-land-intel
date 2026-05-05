#!/usr/bin/env python3
"""
scrape_pmn_archive.py — Phase 12: 24-month PMN backfill scraper.

The standard scrape_utah_pmn.py call against a body page typically returns
only ~10 most recent notices (the public-body sitemap doesn't paginate the
listing far back). This script does a one-time historical backfill: it
loops over every public body declared in jurisdictions.yaml, asks for as
much as the body page exposes, deduplicates against existing data, and
appends new historical notices to data/agendas/ JSON files.

Down-stream pipeline:
  - Output JSONs land in data/agendas/pmn_<body_id>_archive_<date>.json
  - persist_to_csv.py picks them up automatically and merges into
    data/agenda_items.csv with dedup
  - Next weekly-digest run picks up the new historical rows for splitting
    via Haiku and aggregating into city_signal_scores.json

Usage:
  python scripts/scrape_pmn_archive.py                    # all bodies, 24 months
  python scripts/scrape_pmn_archive.py --dry-run          # report counts only
  python scripts/scrape_pmn_archive.py --jurisdiction lehi
  python scripts/scrape_pmn_archive.py --months 12

Estimated runtime: 10–25 minutes for all 26 bodies at 1 req/sec.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Reuse the existing scrape_body() pipeline
sys.path.insert(0, str(Path(__file__).parent))
from scrape_utah_pmn import (  # noqa: E402
    PmnClient,
    scrape_body,
    DEFAULT_USER_AGENT,
    DEFAULT_REQUEST_DELAY_SECONDS,
)
import yaml  # noqa: E402

ROOT = Path(__file__).parent.parent
JURISDICTIONS_YAML = ROOT / "data" / "jurisdictions.yaml"
AGENDAS_DIR = ROOT / "data" / "agendas"
EXISTING_CSV = ROOT / "data" / "agenda_items.csv"
HEARTBEAT_DIR = ROOT / "data" / "cron_status"
UNMATCHED_LOG = ROOT / "data" / "pmn_archive_unmatched.csv"

log = logging.getLogger("scrape_pmn_archive")


def load_pmn_body_ids(jurisdiction_filter: str | None = None) -> list[tuple[str, str, str]]:
    """Returns [(jurisdiction_canonical_name, public_body_label, body_id), ...]."""
    with JURISDICTIONS_YAML.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    out: list[tuple[str, str, str]] = []
    for key, j in (cfg.get("jurisdictions") or {}).items():
        if jurisdiction_filter and key != jurisdiction_filter:
            continue
        canonical = j.get("name", key)
        pmn_bodies = j.get("pmn_body_ids") or {}
        for body_label, body_id in pmn_bodies.items():
            out.append((canonical, body_label, str(body_id)))
    return out


def load_existing_notice_ids() -> set[str]:
    """Existing PMN notice IDs already in agenda_items.csv (dedup target)."""
    if not EXISTING_CSV.exists():
        return set()
    seen: set[str] = set()
    with EXISTING_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rid = row.get("id", "")
            if rid.startswith("pmn_"):
                seen.add(rid[4:])  # strip "pmn_" prefix → notice_id
    return seen


def write_heartbeat(workflow: str, status: str, items: int, duration_ms: int, notes: str = "") -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "workflow_name": workflow,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "duration_ms": duration_ms,
        "items_processed": items,
        "notes": notes,
    }
    (HEARTBEAT_DIR / f"{workflow}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Don't fetch — just report what would be backfilled per body.")
    p.add_argument("--months", type=int, default=24,
                   help="Months back from today (default 24).")
    p.add_argument("--jurisdiction", default=None,
                   help="Limit to one jurisdiction key from jurisdictions.yaml (e.g. 'erda').")
    p.add_argument("--max-notices", type=int, default=None,
                   help="Cap notices per body (debug; default unlimited).")
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--delay", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS,
                   help="Seconds between PMN requests (default 1.0).")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    started = time.time()

    bodies = load_pmn_body_ids(args.jurisdiction)
    if not bodies:
        log.error("No PMN bodies found%s.", f" for jurisdiction={args.jurisdiction}" if args.jurisdiction else "")
        return 1

    log.info("Backfilling %d PMN bodies, months_back=%d (dry_run=%s)",
             len(bodies), args.months, args.dry_run)

    existing_ids = load_existing_notice_ids()
    log.info("Existing PMN notice IDs in agenda_items.csv: %d", len(existing_ids))

    if args.dry_run:
        for canonical, label, body_id in bodies:
            log.info("  [dry-run] %s · %s (id=%s)", canonical, label, body_id)
        log.info("Dry run only. No fetches performed.")
        write_heartbeat("pmn-archive", "success", 0, int((time.time()-started)*1000),
                        f"dry-run · {len(bodies)} bodies")
        return 0

    AGENDAS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    client = PmnClient(user_agent=args.user_agent, delay_seconds=args.delay)
    total_new = 0
    total_seen = 0
    failures: list[str] = []
    unmatched: list[dict[str, str]] = []

    for canonical, label, body_id in bodies:
        log.info("→ %s · %s (id=%s)", canonical, label, body_id)
        try:
            result = scrape_body(
                body_id=body_id,
                client=client,
                months_back=args.months,
                max_notices=args.max_notices,
                jurisdiction_label=canonical,
            )
        except Exception as e:
            log.warning("  Body %s failed: %s — continuing", body_id, e)
            failures.append(f"{canonical} ({body_id}): {e}")
            continue

        notices = result.notices
        new_notices = [n for n in notices if n.notice_id not in existing_ids]
        total_seen += len(notices)
        total_new += len(new_notices)

        if not new_notices:
            log.info("  no new notices (saw %d, all already in CSV)", len(notices))
            continue

        # Persist as JSON for the existing persist_to_csv.py merge step
        out_path = AGENDAS_DIR / f"pmn_{body_id}_archive_{today}.json"
        result_dict = asdict(result)
        # Replace notices list with the new-only subset to keep file size sane
        result_dict["notices"] = [asdict(n) for n in new_notices]
        out_path.write_text(json.dumps(result_dict, indent=2, default=str), encoding="utf-8")
        log.info("  wrote %d new notices → %s", len(new_notices), out_path.name)

        # Track unmatched ones (no event_date_iso) for inspection
        for n in new_notices:
            if not getattr(n, "event_date_iso", None):
                unmatched.append({
                    "jurisdiction": canonical,
                    "body": label,
                    "notice_id": n.notice_id,
                    "title": (n.title or "")[:120],
                    "url": n.notice_url,
                })

    if unmatched:
        UNMATCHED_LOG.parent.mkdir(parents=True, exist_ok=True)
        is_new = not UNMATCHED_LOG.exists()
        with UNMATCHED_LOG.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["jurisdiction","body","notice_id","title","url"])
            if is_new:
                w.writeheader()
            w.writerows(unmatched)
        log.info("Logged %d unmatched (no event_date) notices to %s",
                 len(unmatched), UNMATCHED_LOG.name)

    log.info(
        "Done in %.1fs. %d new notices across %d bodies (saw %d total). %d failures.",
        time.time() - started, total_new, len(bodies), total_seen, len(failures),
    )
    if failures:
        for f in failures:
            log.info("  FAIL: %s", f)

    duration_ms = int((time.time() - started) * 1000)
    status = "partial" if failures else "success"
    write_heartbeat(
        "pmn-archive", status, total_new, duration_ms,
        f"{len(bodies)} bodies, {total_new} new, {len(failures)} failed",
    )

    log.info("\nNext step: run `python scripts/persist_to_csv.py` to merge into agenda_items.csv,")
    log.info("then trigger the weekly-digest workflow to split + correlate the new historical data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
