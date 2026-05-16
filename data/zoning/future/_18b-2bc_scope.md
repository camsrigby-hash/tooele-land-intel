# Phase 18b-2b/c — PDF-path GP FLU cities

Cities that did not yield a public ArcGIS REST future land use layer in Phase 18b-2a. These require the georeferenced PDF pipeline (`gp_pdf_extract.py`) built in Phase 18b-2b and rolled out in Phase 18b-2c.

**Tool**: opusplan (18b-2b prototype on Erda); CC Sonnet + Anthropic Batch API (18b-2c rollout).

| # | City slug | Reason no REST FLU | Probable PDF source | PDF URL | Source quality |
|---|---|---|---|---|---|
| 1 | `erda` | No city GIS portal — parcel GIS is county-hosted | Erda City 2022 adopted General Plan | `https://erda.gov/wp-content/uploads/2022/08/Erda-General-Plan_2022-06-23.pdf` | Good — small city, simple geometry, recent (2022) |
| ~~2~~ | ~~`grantsville`~~ | **MOVED TO REST PATH** — `Future_Land_Use_Map` FeatureServer confirmed 2026-05-16 (51 features, 9 zone types, full city coverage, grade A). Owner org: `gis2_grantsville` (AGOL org `uWdqWzgcb7gCRVuK`). See `_rest_inventory.md`. | N/A | N/A | REST — parcel-detail grade |
| 3 | `bluffdale` | Small city, no GIS data surfaced in REST search | bluffdale.gov GP document | TBD — locate at `bluffdale.gov` | Small/simple city; expected C quality (limited street labels) |
| ~~4~~ | ~~`vineyard`~~ | **MOVED TO REST PATH** — `Vineyard_Future_Land_Use_View` FeatureServer confirmed 2026-05-16 (36 features, 12 zone types, full city coverage). See `_rest_inventory.md`. | N/A | N/A | REST — parcel-detail grade |
| 5 | `draper` | `gis.hlplanning.com` REST root checked; no Draper FLU/GP layer found | Draper City GP document | TBD — locate at `draper.utah.gov` or via Hales Planning | Hales Planning hosts city GIS; PDF may be in planning section |
| 6 | `herriman` | Official GIS page lists General Plan Map but no queryable REST endpoint exposed | Herriman City GP document | TBD — locate at `herriman.gov/government/planning` | City confirmed GP map exists; check for downloadable PDF |
| 7 | `spanish_fork` | `suvgis.spanishfork.org` and download-map-data page checked; no distinct FLU REST found | Spanish Fork GP Land Use document | TBD — confirm at `spanishfork.gov` or `suvgis.spanishfork.org` | Story map at storymaps.arcgis.com (`46123568839342138701884a648c8557`) may link to GP PDF |

## Notes for 18b-2b (Erda prototype)

Erda is the recommended prototype city:
- 2022 adopted GP, PDF URL confirmed above
- Small city area (≈4 sq mi incorporated), simple zone geometry
- Limited street intersections but sufficient for control-point fit
- Expected 4–6 control points, 3–5 major zone classes

The 18b-2b session will build `scripts/gp_pdf_extract.py` and validate end-to-end on Erda before any other city is attempted.

## Notes for 18b-2c (rollout)

Manus or CC Sonnet should locate the GP PDF URLs for the 4 TBD cities above before 18b-2c begins. Add confirmed URLs to this table or to `_rest_inventory.md`. Cities where no GP PDF can be located should be marked `gp_data: not_available` in `_rest_inventory.md` and excluded from 18b-2c — do not attempt extraction without a confirmed source.

RMSE threshold: ≤100 ft accept, 50–100 ft yellow flag, >100 ft reject and document.
Batch API: use Anthropic Batch API for 18b-2c runs (50% cost discount over direct API).
