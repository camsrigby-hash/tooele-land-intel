#!/usr/bin/env python3
"""Scrape every PMN body listed in jurisdictions.yaml."""
import subprocess
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).parent.parent
CFG = yaml.safe_load(open(ROOT / "data" / "jurisdictions.yaml"))
OUTDIR = ROOT / "data" / "agendas"
OUTDIR.mkdir(parents=True, exist_ok=True)

failures = []
for jkey, jcfg in CFG.get("jurisdictions", {}).items():
    body_ids = jcfg.get("pmn_body_ids", {}) or {}
    for body_name, body_id in body_ids.items():
        print(f"  Scraping PMN body {body_id} ({body_name})", file=sys.stderr)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "scrape_utah_pmn.py"),
             "--body-id", str(body_id), "--output-dir", str(OUTDIR)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  [FAIL] {body_name}: {result.stderr[:300]}", file=sys.stderr)
            failures.append(body_name)
        else:
            print(f"  [OK] {result.stdout.strip()}", file=sys.stderr)

if failures:
    print(f"\n{len(failures)} body scrape(s) failed: {failures}", file=sys.stderr)
    sys.exit(1)
