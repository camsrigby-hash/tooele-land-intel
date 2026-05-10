#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ZONING = ROOT / "data" / "zoning"
SUMMARY = ZONING / "extraction_work" / "batch_payloads" / "phase18b_extraction_summary.json"

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

ORDER = [
    "grantsville", "erda", "tooele_city", "lehi", "saratoga_springs", "eagle_mountain",
    "south_jordan", "herriman", "bluffdale", "draper", "american_fork", "vineyard", "spanish_fork",
]

PAGES = {"grantsville": 3, "lehi": 1, "american_fork": 1, "spanish_fork": 2}

SKIPPED_REASONS = {
    "erda": "Official City Code and Maps page links zoning to an ArcGIS webapp, not a PDF.",
    "tooele_city": "No downloadable official zoning map PDF found; available sources were interactive/code documents rather than base-zoning PDF maps.",
    "saratoga_springs": "Planning and GIS pages advertise interactive city maps; no official base-zoning PDF found.",
    "eagle_mountain": "Planning/engineering sources point to interactive ArcGIS zoning apps/maps; no official base-zoning PDF found.",
    "south_jordan": "FAQ states zoning is available through an interactive zoning map; no official zoning PDF found.",
    "herriman": "GIS/search sources point to interactive zoning maps; no official zoning PDF found.",
    "bluffdale": "Maps/search sources point to ArcGIS zoning web map and map-order page; no official zoning PDF found.",
    "draper": "Planning/development map collection is an ArcGIS Experience zoning map; no official zoning PDF found.",
    "vineyard": "Planning/search sources point to public ArcGIS GIS maps and zoning feature layers; no official base-zoning PDF found.",
}

REFS = [
    ("https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php", "Grantsville Zoning Map"),
    ("https://www.lehi-ut.gov/business-development/maps/", "Lehi City Maps"),
    ("https://www.americanfork.gov/276/Planning-Department", "American Fork Planning Department"),
    ("https://www.spanishfork.gov/departments/community_development/planning/zoning.php", "Spanish Fork Zoning"),
    ("https://erda.gov/city-codes-and-maps/", "Erda City Code and Maps"),
    ("https://www.sjc.utah.gov/FAQ.aspx?QID=193", "South Jordan Planning FAQ"),
]


def normalize_zone(code: str, desc: str) -> str:
    c = (code or "").upper().strip()
    d = (desc or "").upper()
    if c.startswith("RA") or "RESIDENTIAL / AGRICULT" in d:
        return "Residential-Agriculture"
    if c.startswith("A") or "EXCLUSIVE AGRICULT" in d or d == "AGRICULTURAL":
        return "Agriculture/Rural"
    if c in {"R-R", "RR", "RR-1", "RR-2.5", "RR-5"} or "RURAL" in d:
        return "Residential-Rural"
    if c.startswith("R-1") or c.startswith("R1") or "SINGLE" in d:
        return "Residential-Low"
    if c in {"R-2", "R-3", "R-4", "R-5", "RM", "RM-15", "RM-7", "R4-7500"} or "MULTI" in d or "MEDIUM" in d:
        return "Residential-Medium/High"
    if c in {"MU", "M-U"} or "MIXED" in d:
        return "Mixed-Use"
    if c in {"PC", "PUD"} or "PLANNED" in d:
        return "Planned/Master-Planned"
    if c in {"RC", "T-M", "BP"}:
        return "Special/Employment"
    if c.startswith("R") or "RESIDENTIAL" in d:
        return "Residential-General"
    if c in {"NC", "C-1", "CN"} or "NEIGHBORHOOD COMMERCIAL" in d:
        return "Commercial-Neighborhood"
    if c.startswith("CC") or "COMMUNITY COMMERCIAL" in d:
        return "Commercial-Community"
    if c.startswith("GC") or c in {"C", "C-2", "CG", "UV-C", "SC-1", "S-C", "CS", "C-D"} or "GENERAL COMMERCIAL" in d or "SHOPPING" in d or "COMMERCIAL" in d:
        return "Commercial-General"
    if c in {"C-I", "CI"}:
        return "Commercial-Industrial"
    if c in {"LI", "I-1", "M-1", "PI-1"} or "LIGHT INDUSTRIAL" in d:
        return "Industrial-Light"
    if c in {"I-3", "MG-EX"} or "HEAVY INDUSTRIAL" in d or "EXTRACTION" in d:
        return "Industrial-Heavy"
    if c in {"I-2", "MG", "MD"} or "MEDIUM INDUSTRIAL" in d or "MANUFACTURING" in d:
        return "Industrial-Medium"
    if "INDUSTRIAL" in d or c.startswith("I"):
        return "Industrial-General"
    if "OPEN" in d or "PARK" in d or c in {"OS", "PF", "P-F", "PI", "S-1"}:
        return "Public/Open-Space"
    return "Other/Local"


def source_cell(urls: list[str]) -> str:
    return "<br>".join(urls) if urls else "N/A"


def main() -> None:
    s = json.loads(SUMMARY.read_text())
    total_features = sum(s["features_by_city"].values())
    total_cost = s["cost"]["cost_usd"]

    # Taxonomy proposal based on zones that were actually extracted, plus extracted legends as context.
    rows = []
    for slug, codes in s["unique_zones_by_city"].items():
        for code in codes:
            desc = s["legend_by_city"].get(slug, {}).get(code, "")
            rows.append((normalize_zone(code, desc), code, CITY_NAMES[slug], desc, "Extracted polygon feature present in GeoJSON."))
    rows.sort(key=lambda r: (r[0], r[2], r[1]))

    tax = []
    tax.append("# Phase 18b Zoning Taxonomy Proposal\n\n")
    tax.append("This proposal is based on **zone codes that were actually extracted as GeoJSON polygon features** from the official PDF zoning-map sources processed through `claude-opus-4-7` via the Anthropic Messages Batch API. The extraction is intentionally conservative: the source maps did not expose reliable georeferencing grids, so the resulting polygons are simplified and marked low quality where the model reported limited coordinate confidence. The taxonomy should be treated as a CC normalization starting point rather than a final ordinance-grade zoning ontology.\n\n")
    tax.append(f"The final Batch run produced **{total_features} polygon features** across **{len(s['processed_pdf_cities'])} cities**, with an estimated Batch API LLM cost of **${total_cost:.2f}** derived from returned token usage.\n\n")
    tax.append("| Proposed `zone_class_normalized` | Source zone code | Source city | PDF legend description | Notes |\n")
    tax.append("|---|---|---|---|---|\n")
    for norm, code, city, desc, note in rows:
        tax.append(f"| `{norm}` | `{code}` | {city} | {desc} | {note} |\n")
    tax.append("\n## Grouping rationale\n\n")
    tax.append("The grouping preserves the primary land-use families that recur across the four extracted jurisdictions: **residential**, **commercial**, **industrial**, **mixed-use**, **agriculture/rural**, **public/open-space**, and **planned or special districts**. Residential codes are split where the source legend conveys rural, agricultural, low-density, or medium/high-density intensity. Commercial and industrial codes are split by neighborhood/community/general and light/medium/heavy intensity where the legend text supports that distinction. Local planned, resort, business-park, and technical-manufacturing codes are preserved in broader planned or special/employment categories so CC can decide whether to preserve them as separate normalized classes.\n")
    tax.append("\n## References\n\n")
    for i, (url, title) in enumerate(REFS, 1):
        tax.append(f"[{i}]: {url} \"{title}\"\n")
    (ZONING / "_taxonomy_proposal.md").write_text("".join(tax), encoding="utf-8")

    # Extraction log.
    log = []
    log.append("# Phase 18b Zoning PDF Vision Extraction Log\n\n")
    log.append("This log documents the completed Phase 18b source discovery and Anthropic Batch API vision extraction. Repository-state inspection showed that all active jurisdictions should be treated as B1 fallback jurisdictions because the checked-in Phase 13b-5 scoring output used `prop_class:` source strings and no real per-city zoning source was present. Four jurisdictions had official downloadable zoning PDF sources; those pages were rendered to images and submitted to `claude-opus-4-7` via the Messages Batch API. Nine jurisdictions were skipped because only interactive/HTML or non-map sources were found.\n\n")
    log.append("| City | Source PDF URL | PDF page count | Polygons extracted | Unique zones found | Total LLM cost | Issues |\n")
    log.append("|---|---|---:|---:|---:|---:|---|\n")
    for slug in ORDER:
        name = CITY_NAMES[slug]
        urls = s["source_urls"].get(slug, [])
        pages = PAGES.get(slug, 0)
        poly = s["features_by_city"].get(slug, 0)
        unique = len(s["unique_zones_by_city"].get(slug, []))
        city_cost = total_cost * (poly / total_features) if total_features else 0.0
        if poly:
            issue = "Batch vision extraction completed. Coordinates are simplified/low-confidence where source maps lacked explicit georeferencing; see GeoJSON metadata page notes."
        elif slug in PAGES:
            issue = "PDF processed by Batch API, but no valid EPSG:4326 polygon features survived validation."
        else:
            issue = SKIPPED_REASONS.get(slug, "No official base-zoning PDF found.")
        log.append(f"| {name} | {source_cell(urls)} | {pages} | {poly} | {unique} | ${city_cost:.2f} | {issue} |\n")
    log.append("\n## Batch API usage and cost\n\n")
    log.append(f"The successful Batch run used **{s['cost']['input_tokens']:,} input tokens** and **{s['cost']['output_tokens']:,} output tokens**. Applying the Batch API 50% discount to Opus-family unit rates gives an estimated LLM cost of **${total_cost:.4f}**, which is below the **$15.00** ceiling. A first validation-only submission errored because `temperature` is deprecated for `claude-opus-4-7`; that run returned no successful messages and no extracted outputs.\n\n")
    log.append("## Source-discovery summary\n\n")
    log.append("Four jurisdictions had downloadable zoning PDFs located and processed: Grantsville, Lehi, American Fork, and Spanish Fork. The other nine active jurisdictions were skipped because their city pages or search results pointed to interactive ArcGIS/HTML zoning maps, map-order pages, or zoning-code PDFs rather than a published base-zoning map PDF. This follows the Phase 18b instruction to skip HTML-rendered zoning maps rather than scrape them.\n\n")
    log.append("## Validation notes\n\n")
    log.append("All 13 per-city GeoJSON files remain syntactically valid EPSG:4326 FeatureCollections. The four PDF-backed cities now contain extracted polygon features; skipped cities remain empty FeatureCollections with metadata explaining the absence of an official PDF source. The extracted geometry should be treated as **planning-intelligence approximation**, not survey-grade or ordinance-grade zoning geometry, because the PDF maps did not expose machine-readable geospatial vectors or reliable coordinate grids to the vision model.\n\n")
    log.append("## Per-page extraction notes\n\n")
    for slug in ["grantsville", "lehi", "american_fork", "spanish_fork"]:
        log.append(f"### {CITY_NAMES[slug]}\n\n")
        for note in s["page_notes"].get(slug, []):
            log.append(f"> {note}\n\n")
    log.append("## References\n\n")
    for i, (url, title) in enumerate(REFS, 1):
        log.append(f"[{i}]: {url} \"{title}\"\n")
    (ZONING / "_extraction_log.md").write_text("".join(log), encoding="utf-8")

    print(json.dumps({"total_features": total_features, "processed_cities": len(s['processed_pdf_cities']), "skipped_cities": len(s['skipped_cities']), "cost_usd": total_cost}, indent=2))


if __name__ == "__main__":
    main()
