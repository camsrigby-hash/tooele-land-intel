#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "data" / "zoning" / "extraction_work"
IMG_DIR = WORK / "page_images"
OUT_DIR = WORK / "batch_payloads"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 12000
API = "https://api.anthropic.com/v1/messages/batches"

PDF_URLS = {
    "american_fork_1": ("american_fork", "American Fork City", "https://www.americanfork.gov/DocumentCenter/View/4139"),
    "grantsville_1": ("grantsville", "Grantsville City", "https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Central%20Area%20June%202025%20(1).pdf"),
    "grantsville_2": ("grantsville", "Grantsville City", "https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Deseret%20Peak%20Area%20June%202025%20(1).pdf"),
    "grantsville_3": ("grantsville", "Grantsville City", "https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Flux%20Area%20June%202025%20(1).pdf"),
    "lehi_1": ("lehi", "Lehi City", "https://www.lehi-ut.gov/media/ywqioi21/lehi_zoning1pdf.pdf"),
    "spanish_fork_1": ("spanish_fork", "Spanish Fork City", "https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Detailed_Letter.pdf"),
    "spanish_fork_2": ("spanish_fork", "Spanish Fork City", "https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Letter.pdf"),
}

PROMPT = """You are extracting base zoning polygons from an official city zoning map page.

Task: inspect the attached zoning-map page image and return ONLY valid JSON matching the schema below. Extract base zoning polygons visible on the map, zone code, and legend description. Use EPSG:4326 longitude/latitude coordinates. If the map page contains a coordinate grid or enough labeled streets/landmarks to infer geographic placement, create approximate but geographically plausible simplified polygons. Close every polygon ring by repeating the first coordinate at the end. Do not include overlay districts or conditional-use overlays. Do not include legend boxes, title boxes, or page decorations as polygons. If a polygon boundary is too detailed, simplify it to the major shape vertices while preserving zone class and approximate location. If reliable geographic placement cannot be inferred for a particular zone area, omit that feature rather than inventing coordinates.

Return this exact JSON object and no markdown:
{
  "city_slug": "...",
  "city_name": "...",
  "source_pdf": "...",
  "page_id": "...",
  "extraction_quality": "high|medium|low|unusable",
  "cost_sensitive_summary": "short note about what was extracted or why omitted",
  "legend": [{"zone_code":"...", "zone_description":"..."}],
  "features": [
    {
      "zone_code": "...",
      "zone_description": "...",
      "geometry": {"type":"Polygon", "coordinates":[[[lon,lat],[lon,lat],[lon,lat],[lon,lat]]]},
      "confidence": 0.0
    }
  ]
}

Important constraints: coordinates must be longitude/latitude decimal degrees in Utah; coordinate values outside plausible Utah ranges are invalid. A feature's zone_code must appear in the legend array. Prefer fewer reliable simplified polygons over many unreliable tiny polygons."""


def image_block(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def main() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is required")

    requests_payload = []
    manifest = []
    for img in sorted(IMG_DIR.glob("*.jpg")):
        page_id = img.stem
        if page_id not in PDF_URLS:
            continue
        slug, city_name, source_pdf = PDF_URLS[page_id]
        custom_id = f"{slug}_{page_id}"
        text = f"City slug: {slug}\nCity name: {city_name}\nSource PDF: {source_pdf}\nPage ID: {page_id}\n\n{PROMPT}"
        params = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": [image_block(img), {"type": "text", "text": text}]}],
        }
        requests_payload.append({"custom_id": custom_id, "params": params})
        manifest.append({"custom_id": custom_id, "image": str(img), "city_slug": slug, "city_name": city_name, "source_pdf": source_pdf})

    payload = {"requests": requests_payload}
    (OUT_DIR / "phase18b_batch_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Do not write the base64-heavy request payload to disk unless debugging is explicitly needed.
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    resp = requests.post(API, headers=headers, json=payload, timeout=120)
    print("status", resp.status_code)
    print(resp.text[:4000])
    resp.raise_for_status()
    data = resp.json()
    (OUT_DIR / "phase18b_batch_submission.json").write_text(json.dumps(data, indent=2) + "\n")
    print("batch_id", data.get("id"))
    print("request_count", len(requests_payload))


if __name__ == "__main__":
    main()
