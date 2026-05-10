#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "zoning" / "source_pdfs"
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "grantsville": {
        "page": "https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php",
        "urls": [
            "https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Central%20Area%20June%202025%20(1).pdf",
            "https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Deseret%20Peak%20Area%20June%202025%20(1).pdf",
            "https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Flux%20Area%20June%202025%20(1).pdf",
        ],
    },
    "lehi": {
        "page": "https://www.lehi-ut.gov/business-development/maps/",
        "urls": ["https://www.lehi-ut.gov/media/ywqioi21/lehi_zoning1pdf.pdf"],
    },
    "spanish_fork": {
        "page": "https://www.spanishfork.gov/departments/community_development/planning/zoning.php",
        "urls": [
            "https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Detailed_Letter.pdf",
            "https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Letter.pdf",
        ],
    },
    "american_fork": {
        "page": "https://www.americanfork.gov/276/Planning-Department",
        "urls": ["https://www.americanfork.gov/DocumentCenter/View/4139"],
    },
}

# Jurisdictions verified as interactive-only or no official PDF found during source discovery.
NO_PDF = {
    "erda": "Official City Code and Maps page links Zoning Map to an ArcGIS webapp, not a PDF.",
    "saratoga_springs": "Planning and Mapping/GIS pages advertise interactive city maps; no official base-zoning PDF found in source discovery.",
    "eagle_mountain": "Planning/engineering pages and search results point to interactive ArcGIS zoning app/maps; no official base-zoning PDF found.",
    "south_jordan": "FAQ states zoning is available through an interactive zoning map; no official zoning PDF found.",
    "herriman": "GIS page/search results point to interactive zoning map; no official zoning PDF found.",
    "bluffdale": "Maps page and search results point to ArcGIS zoning web map and map-order page; no official zoning PDF found.",
    "draper": "Planning/development map collection is an ArcGIS Experience zoning map; no official zoning PDF found.",
    "tooele_city": "City maps page found, but no downloadable official zoning map PDF found in source discovery; available sources appear to be interactive or code PDFs rather than the zoning map.",
    "vineyard": "Planning page/search results point to public ArcGIS GIS maps and zoning feature layer; no official base-zoning PDF found.",
}


def download(url: str, dest: Path) -> tuple[bool, str]:
    try:
        r = requests.get(url, timeout=40, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}"
        content_type = r.headers.get("content-type", "")
        data = r.content
        if not data.startswith(b"%PDF") and "pdf" not in content_type.lower():
            return False, f"not_pdf content_type={content_type} bytes={len(data)}"
        dest.write_bytes(data)
        return True, f"ok bytes={len(data)} content_type={content_type}"
    except Exception as exc:
        return False, f"error {exc!r}"


def main() -> int:
    records = []
    for slug, meta in SOURCES.items():
        for i, url in enumerate(meta["urls"], 1):
            suffix = "pdf"
            dest = OUT / f"{slug}_{i}.{suffix}"
            ok, note = download(url, dest)
            records.append({
                "city_slug": slug,
                "page": meta["page"],
                "source_url": url,
                "local_path": str(dest if ok else ""),
                "downloaded": ok,
                "note": note,
            })
    for slug, reason in NO_PDF.items():
        records.append({
            "city_slug": slug,
            "page": "",
            "source_url": "",
            "local_path": "",
            "downloaded": False,
            "note": reason,
        })
    (ROOT / "data" / "zoning" / "extraction_work" / "source_discovery.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    for rec in records:
        print(json.dumps(rec))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
