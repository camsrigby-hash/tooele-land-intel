#!/usr/bin/env bash
#
# cm_re_extract.sh — Unpack reusable CM_RE modules into tooele-land-intel/vendor/cm_re/
#
# PRE-REQUISITE:
#   - Run this from the root of your tooele-land-intel clone in Codespaces
#     (or any terminal with the two zip files downloaded)
#   - CM_RE_Tool_Code.zip in ~/Downloads/ (or pass a different path as $1)
#
# USAGE:
#   bash cm_re_extract.sh                          # uses ~/Downloads/CM_RE_Tool_Code.zip
#   bash cm_re_extract.sh /path/to/CM_RE_Tool_Code.zip
#
# WHAT IT DOES:
#   1. Creates vendor/cm_re/ read-only reference tree
#   2. Extracts only the modules listed in docs/CM_RE_INTEGRATION.md §4
#   3. Leaves the CRE scorer, competition, shortlist, owner-scrape,
#      NAIP-chip pipeline, and 287MB of exhibit PDFs in the zip
#   4. Writes vendor/cm_re/README.md explaining what's here and why
#
# IDEMPOTENT: safe to re-run; will overwrite the reference tree.

set -euo pipefail

ZIP_PATH="${1:-$HOME/Downloads/CM_RE_Tool_Code.zip}"
VENDOR_DIR="vendor/cm_re"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "▶ cm_re_extract.sh"
echo "  zip:     $ZIP_PATH"
echo "  vendor:  $VENDOR_DIR"
echo

# ── 0. Sanity checks ──────────────────────────────────────────────────────────

if [[ ! -f "$ZIP_PATH" ]]; then
  echo "✗ Zip not found at $ZIP_PATH" >&2
  echo "  Pass the path as the first argument, or move the zip to ~/Downloads/." >&2
  exit 1
fi

if [[ ! -d ".git" ]]; then
  echo "✗ This doesn't look like a git repo root. cd into tooele-land-intel/ first." >&2
  exit 1
fi

command -v unzip >/dev/null || { echo "✗ unzip not installed" >&2; exit 1; }

# ── 1. Unzip to temp ──────────────────────────────────────────────────────────

echo "▶ Extracting zip to temp..."
unzip -q "$ZIP_PATH" -d "$TMP_DIR"
echo "  extracted to $TMP_DIR"
echo

# Validate expected layout
RE_SRC="$TMP_DIR/RE_Identification_Tool"
MANUS_SRC="$TMP_DIR/Manus_RE_Tool"
if [[ ! -d "$RE_SRC" || ! -d "$MANUS_SRC" ]]; then
  echo "✗ Expected RE_Identification_Tool/ and Manus_RE_Tool/ in the zip." >&2
  echo "  Got: $(ls "$TMP_DIR")" >&2
  exit 1
fi

# ── 2. Build vendor/cm_re/ tree ───────────────────────────────────────────────

echo "▶ Building $VENDOR_DIR tree..."
rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"/{scraper,parcel,stip,land_cover,docs}

# Scraper + parser + aggregator (from RE_Identification_Tool)
cp "$RE_SRC/scraper.py"      "$VENDOR_DIR/scraper/"
cp "$RE_SRC/parser.py"       "$VENDOR_DIR/scraper/"
cp "$RE_SRC/aggregator.py"   "$VENDOR_DIR/scraper/"
cp "$RE_SRC/config.py"       "$VENDOR_DIR/scraper/"

# Parcel + road adjacency + polygon map (from Manus_RE_Tool)
cp "$MANUS_SRC/parcel_fetcher.py"       "$VENDOR_DIR/parcel/"
cp "$MANUS_SRC/parcel_polygon_map.py"   "$VENDOR_DIR/parcel/"
cp "$MANUS_SRC/road_adjacency.py"       "$VENDOR_DIR/parcel/"

# STIP
cp "$RE_SRC/stip_fetcher.py"  "$VENDOR_DIR/stip/"

# Land cover (Phase 10 — deferred)
cp "$MANUS_SRC/land_cover_analyzer.py"   "$VENDOR_DIR/land_cover/"
cp "$RE_SRC/spectral_classifier.py"      "$VENDOR_DIR/land_cover/"

# Original project docs for context
cp "$RE_SRC/README.md"                    "$VENDOR_DIR/docs/RE_Identification_Tool_README.md"
cp "$MANUS_SRC/README.md"                 "$VENDOR_DIR/docs/Manus_RE_Tool_README.md"
cp "$MANUS_SRC/PROJECT_STATUS_v4.md"      "$VENDOR_DIR/docs/"

echo "  wrote $(find "$VENDOR_DIR" -type f | wc -l) files"
echo

# ── 3. Mark vendor tree as read-only reference ────────────────────────────────

echo "▶ Writing vendor/cm_re/README.md..."

cat > "$VENDOR_DIR/README.md" <<'VENDOR_README'
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
VENDOR_README

# Make everything under vendor/cm_re read-only at the filesystem level as a
# belt-and-braces reminder. Easy to override with chmod if someone actually
# needs to edit.
chmod -R a-w "$VENDOR_DIR" 2>/dev/null || true
chmod u+w "$VENDOR_DIR"          # keep write on the top dir so we can re-run

echo "  marked $VENDOR_DIR read-only"
echo

# ── 5. Summary ────────────────────────────────────────────────────────────────

echo "▶ Summary"
echo "  total files: $(find "$VENDOR_DIR" -type f | wc -l)"
echo "  total size:  $(du -sh "$VENDOR_DIR" | cut -f1)"
echo
echo "  Tree:"
find "$VENDOR_DIR" -type f | sort | sed 's/^/    /'
echo

echo "✓ CM_RE reference extracted to $VENDOR_DIR"
echo
echo "Next steps (see docs/PROMPT_PLAYBOOK_ADDENDUM.md):"
echo "  1. Phase 1 addendum — parser schema upgrade"
echo "  2. PMN discovery — visit https://www.utah.gov/pmn/sitemap/index.html"
echo "     and note body IDs for Erda, Grantsville, and expansion cities"
echo "  3. Phase 3+ addenda — port UGRC patterns as each phase comes up"
