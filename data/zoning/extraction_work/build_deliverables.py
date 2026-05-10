#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ZONING = ROOT / "data" / "zoning"
DISCOVERY = json.loads((ZONING / "extraction_work" / "source_discovery.json").read_text())
EXTRACTED_AT = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

CITY_NAMES = {
    "grantsville": "Grantsville City",
    "erda": "Erda City",
    "tooele_city": "Tooele City",
    "lehi": "Lehi City",
    "saratoga_springs": "Saratoga Springs City",
    "eagle_mountain": "Eagle Mountain City",
    "south_jordan": "South Jordan City",
    "herriman": "Herriman City",
    "bluffdale": "Bluffdale City",
    "draper": "Draper City",
    "american_fork": "American Fork City",
    "vineyard": "Vineyard City",
    "spanish_fork": "Spanish Fork City",
}

PDF_PAGES = {
    "american_fork": 1,
    "grantsville": 3,
    "lehi": 1,
    "spanish_fork": 2,
}

# Legend text extracted with pdftotext from PDFs that were downloaded. These are included in
# the taxonomy proposal only as a starting point; no polygons were generated from them.
LEGEND_CODES = {
    "lehi": [
        ("A-5", "Agricultural", "Agriculture/Rural"),
        ("A-1", "Agricultural", "Agriculture/Rural"),
        ("RA-1", "Residential / Agriculture", "Residential-Agriculture"),
        ("R-1-22", "Residential / Agriculture", "Residential-Low"),
        ("R-1-15", "Residential", "Residential-Low"),
        ("R-1-12", "Residential", "Residential-Low"),
        ("R-1-10", "Residential", "Residential-Low"),
        ("R-1-8", "Residential", "Residential-Low"),
        ("R-2", "Medium Density Residential", "Residential-Medium"),
        ("R-3", "High Density Residential", "Residential-High"),
        ("MU", "Mixed Use Commercial/Residential", "Mixed-Use"),
        ("NC", "Neighborhood Commercial", "Commercial-Neighborhood"),
        ("C", "Commercial", "Commercial-General"),
        ("H/I", "Historical / Industrial", "Industrial-Historic"),
        ("LI", "Light Industrial", "Industrial-Light"),
        ("I", "Industrial", "Industrial-General"),
        ("C-I", "Commercial / Industrial", "Commercial-Industrial"),
    ],
    "spanish_fork": [
        ("R-R", "Rural Residential", "Residential-Rural"),
        ("R-1-80", "Residential District", "Residential-Low"),
        ("R-1-60", "Residential District", "Residential-Low"),
        ("R-1-40", "Residential District", "Residential-Low"),
        ("R-1-30", "Residential District", "Residential-Low"),
        ("R-1-20", "Residential District", "Residential-Low"),
        ("R-1-15", "Residential District", "Residential-Low"),
        ("R-1-12", "Residential District", "Residential-Low"),
        ("R-1-9", "Residential District", "Residential-Low"),
        ("R-1-8", "Residential District", "Residential-Low"),
        ("R-1-6", "Residential District", "Residential-Low"),
        ("R-3", "Residential District", "Residential-Medium"),
        ("R-4", "Residential District", "Residential-Medium"),
        ("R-5", "Residential District", "Residential-High"),
        ("R-O", "Residential Office", "Residential-Office"),
        ("C-O", "Commercial Office", "Commercial-Office"),
        ("C-1", "Neighborhood Commercial", "Commercial-Neighborhood"),
        ("C-2", "General Commercial", "Commercial-General"),
        ("UV-C", "Urban Village Commercial", "Commercial-General"),
        ("I-1", "Light Industrial", "Industrial-Light"),
        ("I-2", "Medium Industrial", "Industrial-Medium"),
        ("I-3", "Heavy Industrial", "Industrial-Heavy"),
    ],
}

SOURCE_BY_CITY = {}
for rec in DISCOVERY:
    slug = rec["city_slug"]
    SOURCE_BY_CITY.setdefault(slug, []).append(rec)

# Empty GeoJSON FeatureCollections for every fallback jurisdiction; avoiding fabricated geometry.
for slug, name in CITY_NAMES.items():
    geojson = {
        "type": "FeatureCollection",
        "name": f"{slug}_zoning",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [],
        "metadata": {
            "city_slug": slug,
            "city_name": name,
            "extracted_at": EXTRACTED_AT,
            "extraction_status": "skipped_no_pdf_or_no_vision_secret",
            "notes": "No zone polygons were written because the run could not submit required Anthropic Batch API vision calls and did not fabricate geometry.",
            "source_urls": [r["source_url"] for r in SOURCE_BY_CITY.get(slug, []) if r.get("source_url")],
        },
    }
    (ZONING / f"{slug}.geojson").write_text(json.dumps(geojson, indent=2) + "\n", encoding="utf-8")

# Taxonomy proposal.
rows = []
for city, codes in LEGEND_CODES.items():
    for code, desc, norm in codes:
        rows.append((code, city, desc, norm, "Legend text extracted from downloaded zoning PDF; awaiting polygon extraction."))
rows = sorted(set(rows), key=lambda r: (r[3], r[0], r[1]))

tax = []
tax.append("# Phase 18b Zoning Taxonomy Proposal\n")
tax.append("This proposal is intentionally limited to **legend codes that could be read from downloaded zoning PDFs without running vision extraction**. The local task environment did not expose `ANTHROPIC_API_KEY`, so no Opus Batch API vision calls were submitted and no polygon geometry was inferred from PDF page coordinates. The table is therefore a draft normalization aid for CC and should be reviewed after polygon extraction is rerun in an environment with the required Anthropic secret.\n")
tax.append("| Source zone code | Source city | PDF legend description | Proposed `zone_class_normalized` | Notes |\n")
tax.append("|---|---|---|---|---|\n")
for code, city, desc, norm, note in rows:
    tax.append(f"| `{code}` | {CITY_NAMES.get(city, city)} | {desc} | `{norm}` | {note} |\n")
tax.append("\n## Proposed grouping rationale\n\nThe initial grouping keeps commercial, residential, mixed-use, industrial, and agriculture/rural categories separate, while preserving intensity where the source legend states or strongly implies intensity. Similar codes such as Spanish Fork `C-2` and Lehi `C` are grouped under `Commercial-General`; Spanish Fork `I-1` and Lehi `LI` are grouped under `Industrial-Light`; and low-density single-family residential codes are grouped under `Residential-Low`. The proposal does **not** resolve local overlay, planned-community, or conditional-use distinctions because Phase 18b is scoped to base zoning only.\n")
tax.append("\n## References\n\n")
tax.append("[1]: https://www.lehi-ut.gov/business-development/maps/ \"Lehi City Business Development Maps\"\n")
tax.append("[2]: https://www.spanishfork.gov/departments/community_development/planning/zoning.php \"Spanish Fork Zoning\"\n")
tax.append("[3]: https://www.americanfork.gov/276/Planning-Department \"American Fork Planning Department\"\n")
tax.append("[4]: https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php \"Grantsville Zoning Map\"\n")
(ZONING / "_taxonomy_proposal.md").write_text("".join(tax), encoding="utf-8")

# Extraction log.
log = []
log.append("# Phase 18b Zoning PDF Vision Extraction Log\n")
log.append("\nThis log documents the Phase 18b source discovery and extraction attempt. Repository-state inspection showed that all active jurisdictions should be treated as B1 fallback jurisdictions, because the checked-in Phase 13b-5 scoring output used `prop_class:` source strings and no real per-city zoning source was present. The local execution environment did not expose `ANTHROPIC_API_KEY`, and the available GitHub token could not inspect repository Actions secrets, so the required Anthropic Batch API vision extraction was not submitted. To preserve data integrity, this run wrote valid empty GeoJSON FeatureCollections rather than fabricating polygon coordinates from PDF page/image space.\n")
log.append("\n| City | Source PDF URL | PDF page count | Polygons extracted | Unique zones found | Total LLM cost | Issues |\n")
log.append("|---|---|---:|---:|---:|---:|---|\n")
for slug, name in CITY_NAMES.items():
    recs = SOURCE_BY_CITY.get(slug, [])
    urls = [r["source_url"] for r in recs if r.get("source_url")]
    source_cell = "<br>".join(urls) if urls else "N/A"
    pages = PDF_PAGES.get(slug, 0)
    unique = len({c for c, _, _ in LEGEND_CODES.get(slug, [])})
    if urls:
        issue = "PDF located and downloaded; vision extraction skipped because `ANTHROPIC_API_KEY` was not available locally. No polygons fabricated."
    else:
        notes = "; ".join(r.get("note", "") for r in recs) if recs else "No source record."
        issue = notes
    log.append(f"| {name} | {source_cell} | {pages} | 0 | {unique} | $0.00 | {issue} |\n")
log.append("\n## Source-discovery summary\n\nFour jurisdictions had downloadable zoning PDFs located during this run: Grantsville, Lehi, American Fork, and Spanish Fork. The other nine active jurisdictions were flagged because their city pages or search results pointed to interactive ArcGIS/HTML zoning maps, map-order pages, or zoning-code PDFs rather than a published base-zoning map PDF. This follows the instruction to skip HTML-rendered zoning maps rather than scrape them.\n")
log.append("\n## Validation notes\n\nAll generated GeoJSON files are syntactically valid FeatureCollections in EPSG:4326, but they intentionally contain zero features because no trusted polygon coordinates were produced. A future rerun with `ANTHROPIC_API_KEY` available should replace these empty collections with closed Polygon features and then validate that every `zone_code` appears in the source PDF legend.\n")
log.append("\n## References\n\n")
refs = [
    ("https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php", "Grantsville Zoning Map"),
    ("https://www.lehi-ut.gov/business-development/maps/", "Lehi City Maps"),
    ("https://www.americanfork.gov/276/Planning-Department", "American Fork Planning Department"),
    ("https://www.spanishfork.gov/departments/community_development/planning/zoning.php", "Spanish Fork Zoning"),
    ("https://erda.gov/city-codes-and-maps/", "Erda City Code and Maps"),
    ("https://www.sjc.utah.gov/FAQ.aspx?QID=193", "South Jordan Planning FAQ"),
]
for i, (url, title) in enumerate(refs, 1):
    log.append(f"[{i}]: {url} \"{title}\"\n")
(ZONING / "_extraction_log.md").write_text("".join(log), encoding="utf-8")

print(f"Generated deliverables at {ZONING}")
print(f"GeoJSON files: {len(CITY_NAMES)}")
print("LLM cost: $0.00")
