# Prompt Playbook Addendum — tooele-land-intel

Supplements the main opusplan/CC Sonnet session prompts with phase-specific context and kickoff prompts.

---

## CURRENT STATE

**Active phase: 18b-2b** — GP FLU PDF pipeline prototype (Erda City).

- 18b-1 current zoning: shipped for 12/13 cities. Saratoga Springs UT re-extraction outstanding (18b-1 hotfix).
- 18b-2a GP FLU REST: shipped for 6 cities. NLS source authority flagged for Lehi/Eagle Mountain/Saratoga Springs GP.
- 18b-2b: **start here** — build `scripts/gp_pdf_extract.py`, validate on Erda City 2022 GP PDF.
- 18b-2c: rollout to 6 remaining cities after 18b-2b prototype accepted.
- 18b-3: D1 load — after all 13 cities have both current zoning and GP FLU data.

---

## Phase 18b-2b Kickoff Prompt — Erda Prototype

```
Phase 18b-2b: GP FLU PDF pipeline prototype — Erda City

CONTEXT
=======
Tooele-land-intel needs General Plan Future Land Use (GP FLU) polygons for 7 cities
that have no public ArcGIS REST FLU layer. Phase 18b-2a already extracted REST data for
6 cities. The remaining 7 (Erda, Grantsville, Bluffdale, Vineyard, Draper, Herriman,
Spanish Fork) must be georeferenced from PDF maps.

Phase 18b-2b: build and validate the PDF-to-GeoJSON pipeline using Erda as the prototype.

ERDA SOURCE
===========
PDF: https://erda.gov/wp-content/uploads/2022/08/Erda-General-Plan_2022-06-23.pdf
Adoption date: 2022-06-23 (most recent adopted GP)
City area: ~4 sq mi incorporated
Expected: 4–6 control points, 3–5 major zone classes (Agricultural, Residential, Commercial,
          Industrial, Public/Institutional at minimum)
City center approx: lat 40.582, lon -112.298

DELIVERABLE
===========
1. scripts/gp_pdf_extract.py — generalized PDF georeferencing pipeline that:
   a. Downloads the PDF (or accepts a local path)
   b. Renders pages to high-res image (≥200 dpi)
   c. Identifies the land use / FLU map page (may be in appendix or exhibit)
   d. Accepts a set of control points (lng/lat ↔ pixel x/y) and computes an affine
      or thin-plate spline transform (prefer affine for simple small-city maps)
   e. Traces zone polygons from the rendered image (color segmentation, contour detection,
      or manual WKT input — choose the approach that works reliably for small-city PDFs)
   f. Reprojects to EPSG:4326
   g. Outputs a GeoJSON with schema:
      { city_slug, city_name, gp_zone_code, gp_zone_description, gp_zone_normalized,
        jurisdiction, source_pdf_url, source_pdf_page, extraction_method, confidence,
        rmse_ft (float), control_point_count (int) }

2. data/zoning/future/erda_gp.geojson — extracted from the 2022 Erda GP PDF
   - Centroid must be within 5 km of (40.582, -112.298)
   - RMSE ≤ 100 ft (accept), 50–100 ft (yellow flag), >100 ft (reject)
   - All polygons valid (shapely is_valid after buffer(0))
   - Coordinates in EPSG:4326 Utah range (lng [-114,-109], lat [37,42])

3. data/zoning/future/_pdf_extraction_log.md — per-city log entry for Erda:
   - PDF URL, page number, control point count, RMSE, zone class list,
     feature count, any manual interventions

TECHNICAL NOTES
===============
- Python stack: use shapely, Pillow (PIL), numpy, scipy, pyproj, and optionally
  opencv-python for image processing. All are pip-installable.
- For affine transform: scipy.interpolate or skimage.transform.AffineTransform
- For color segmentation: k-means on RGB or HSV, or manual hex-code zone color list
  if the PDF has a clear legend
- Control points: pick intersections of named roads visible on both the PDF and
  known coordinates (use OpenStreetMap or UGRC base map for reference)
- Minimum 4 control points; prefer 6+ for thin-plate spline
- If polygon tracing is unreliable (fuzzy colors, thin boundary lines), accept
  manual WKT input of zone polygons from the operator as an alternative path
- The script should be reusable for all 18b-2c cities — parameterize city_slug,
  pdf_url, page_number, control_points, zone_color_map

QUALITY GATES
=============
- Run the sanity check from Phase 18b verification:
  python scripts/sanity_check_geojson.py data/zoning/future/erda_gp.geojson
  (centroid < 5 km, bbox < 30 km, all coords in Utah range, is_valid)
- RMSE threshold: ≤100 ft PASS, 50–100 ft WARN, >100 ft FAIL
- If FAIL: document in log, do not commit the GeoJSON, recommend a different
  control point set or a different extraction approach

BRANCH
======
Work on branch: phase-18b-2b-pdf-pipeline
Commit the script (scripts/gp_pdf_extract.py) and Erda output (data/zoning/future/erda_gp.geojson)
as separate commits. Do not merge to main until RMSE and sanity check both pass.

SCOPE LIMIT
===========
This session covers ONLY Erda. Do not attempt other cities. If the pipeline works on
Erda, the 18b-2c session will run the 6 remaining cities.
```

---

## Phase 18b-2c Kickoff Notes

Before starting 18b-2c, confirm PDF URLs for:
- Grantsville: 3-part FLU map (Jan 2020) in Document Center at `grantsvilleut.gov`
- Bluffdale: GP PDF at `bluffdale.gov/DocumentCenter/View/5049/Bluffdale-General-Plan-PDF`
- Vineyard: FLU map at `vineyardutah.gov/Departmnts/Planning/Future Land Use Map.pdf` — **check ArcGIS Experience first** (`experience.arcgis.com/experience/5d675261cad649ffb85deee52dcbe1cb/`) for a REST endpoint before PDF path
- Draper: TBD — check `draper.utah.gov` or Hales Planning
- Herriman: TBD — `herriman.gov/government/planning`
- Spanish Fork: TBD — `spanishfork.gov` or ArcGIS Story Map `46123568839342138701884a648c8557`

Use Anthropic Batch API (50% cost discount) for the 18b-2c multi-city run.
RMSE threshold: ≤100 ft accept, 50–100 ft yellow flag, >100 ft reject and document.

---

## Phase 18b-3 Pre-work Checklist

Before D1 load:
- [ ] Saratoga Springs UT current zoning re-extracted (18b-1 hotfix)
- [ ] All 7 PDF-path cities have accepted GeoJSONs (RMSE ≤ 100 ft)
- [ ] Lehi taxonomy remapping complete: reduce Other/Unknown from 42% to <5%
- [ ] Tooele City GP multi-zone comma-separated codes handled (Option A or B decided)
- [ ] NLS source authority verified for Lehi, Eagle Mountain, Saratoga Springs GP — or marked `source_authority: unverified_regional_study`
- [ ] D1 migration `0006_gp_zoning.sql` drafted and reviewed
