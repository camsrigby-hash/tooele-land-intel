# vendor/cm_re/ — READ-ONLY REFERENCE

This directory contains source files from a prior related project ("CM_RE",
commercial real estate site-selection tool for Davis + Weber counties, Utah).

**These files are reference material. DO NOT import from this directory at
runtime in any TLI module.** Instead:

1. Read the module here to understand the pattern.
2. Port the pattern into a new TLI-native module under `scripts/` or
   wherever it belongs.
3. Adapt field schemas, configuration, and data sources to TLI's Tooele
   Valley / Wasatch Front scope.

## Why it's here

See `docs/CM_RE_INTEGRATION.md` in the wasatch-intel repo. Short version:
CM_RE already built robust versions of the scraper, parser, aggregator, UGRC
fetcher, road adjacency, STIP ingestion, and NAIP land cover infrastructure
that TLI needs for Phases 3–6 and 9–10.

## What's included

- `scraper/` — utah.gov/pmn scraping, Claude-based PDF parsing, city-level
  weighted aggregation, Davis+Weber PMN body ID reference
- `parcel/` — UGRC LIR resilient fetcher, parcel polygon map renderer,
  road adjacency with real AADT data
- `stip/` — UDOT future road project ingestion
- `land_cover/` — NAIP spectral classification (Phase 10, deferred)
- `docs/` — original project READMEs and final status doc for context

## What's NOT included (and why)

Left in the original zip, intentionally skipped:

- `parcel_scorer.py`, `competition.py` — gas-station/miniflex scoring, Google
  Places API. TLI is intel, not site-selection.
- `scrape_owners.py` — county assessor scraping. TLI uses UGRC LIR
  ownership fields directly; county-side scraping has legal/CAPTCHA issues.
- `commute_corridor.py`, `visual_scanner.py` — gas-station-specific logic.
- `tools/generate_shortlist.py`, `tools/rank_outreach.py`,
  `tools/outreach_map.py` — CRE outreach call-sheet generation.
- `naip_fetcher.py` — superseded by `land_cover_analyzer.py`'s Microsoft
  Planetary Computer path.
- The 287MB `CM_RE_Tool_Exhibits.zip` — raw PMN-downloaded meeting PDFs,
  not source code.

## Provenance

Source zip: `CM_RE_Tool_Code.zip` (~139 MB before filtering)
Extracted: via `cm_re_extract.sh` from the wasatch-intel addendum pack
Generated: on the user's first Codespaces session after receiving the pack
