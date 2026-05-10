#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "data" / "zoning" / "extraction_work"
OUT_DIR = WORK / "batch_payloads"
SUBMISSION = OUT_DIR / "phase18b_batch_submission.json"
STATUS = OUT_DIR / "phase18b_batch_status.json"
RESULTS = OUT_DIR / "phase18b_batch_results.jsonl"

API = "https://api.anthropic.com/v1/messages/batches"


def main() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is required")
    batch_id = json.loads(SUBMISSION.read_text())["id"]
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    resp = requests.get(f"{API}/{batch_id}", headers=headers, timeout=60)
    print("status", resp.status_code)
    print(resp.text[:4000])
    resp.raise_for_status()
    data = resp.json()
    STATUS.write_text(json.dumps(data, indent=2) + "\n")
    if data.get("results_url"):
        r = requests.get(data["results_url"], headers=headers, timeout=120)
        print("results_status", r.status_code, "bytes", len(r.content))
        print(r.text[:2000])
        r.raise_for_status()
        RESULTS.write_bytes(r.content)
        print("wrote", RESULTS)


if __name__ == "__main__":
    main()
