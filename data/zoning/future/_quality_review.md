# GP FLU Quality Review — PDF-Path Cities

Tracks acceptance/rejection decisions for each city processed through the `gp_pdf_extract.py` pipeline (Phase 18b-2b/c).

**RMSE thresholds** (per 18b-2bc_scope.md):
- PASS: <= 100 ft
- YELLOW FLAG: 50–100 ft (accept with note)
- FAIL: > 100 ft (reject; document reason)

---

## Erda

| Field | Value |
|---|---|
| Status | **FAIL — regional map, not parcel-level** |
| Phase | 18b-2b (prototype) |
| Date | 2026-05-14 |
| RMSE | 4664 ft |
| Control points | 5 (place names; no street intersections labeled) |
| GeoJSON | `erda_gp.geojson` (0 features) |
| Validation report | `erda_transform_validation.md` |

**Reason for failure**: The Erda 2022 General Plan Future Land Use map (PDF p. 21) is a regional context overview spanning ~11 miles east-west and ~9 miles north-south. At this scale (~26 ft/px theoretical, ~100 ft/px effective after distortion), parcel-level georeferencing is physically impossible. The pipeline ran all 8 stages successfully — the limitation is the source data, not the pipeline.

**Recommendation**: Mark `gp_data: regional_map_only` in `_rest_inventory.md`. Investigate whether Tooele County or Erda City publishes a higher-detail FLU exhibit (e.g., a zoning map at 1:10,000 scale). If not found, Erda will remain without GP FLU coverage until a suitable source is identified.

**Pipeline validation notes**: Despite 0 usable features, Phase 18b-2b achieved its prototype goal:
- All 8 stages of `gp_pdf_extract.py` executed without errors
- Text-scan page detection (`find_map_page_hints`) correctly identified p. 21
- Stage 6 (legend + zone extraction) identified 8 zone classes and 36 polygon attempts
- Stage 7 bounds filtering correctly rejected out-of-bounds projections
- Stage 8 wrote valid GeoJSON (0-feature FeatureCollection with correct metadata)
- API cost logging (erda_api_calls.jsonl) captured all 9 Haiku calls at $0.3212

---

## Grantsville

| Field | Value |
|---|---|
| Status | **PENDING — 18b-2c** |
| Phase | 18b-2c |
| PDF URL | TBD (locate at grantsvilleut.gov) |
| Notes | Water Element confirms GP amended through Oct 2025; FLU map is a separate exhibit. Recommended as first 18b-2c city. |

---

## Bluffdale

| Field | Value |
|---|---|
| Status | **PENDING — 18b-2c** |
| Phase | 18b-2c |
| PDF URL | TBD (locate at bluffdale.gov) |
| Notes | Small/simple city; expected source quality C (limited street labels). |

---

## Draper

| Field | Value |
|---|---|
| Status | **PENDING — 18b-2c** |
| Phase | 18b-2c |
| PDF URL | TBD (locate at draper.utah.gov or via Hales Planning) |
| Notes | Hales Planning hosts city GIS; PDF may be in planning section. |

---

## Herriman

| Field | Value |
|---|---|
| Status | **PENDING — 18b-2c** |
| Phase | 18b-2c |
| PDF URL | TBD (locate at herriman.gov/government/planning) |
| Notes | City confirmed GP map exists; check for downloadable PDF. |

---

## Spanish Fork

| Field | Value |
|---|---|
| Status | **PENDING — 18b-2c** |
| Phase | 18b-2c |
| PDF URL | TBD (confirm at spanishfork.gov or suvgis.spanishfork.org) |
| Notes | Story map at storymaps.arcgis.com may link to GP PDF. |

---

## Prerequisites for 18b-2c

Before 18b-2c begins:

1. **Locate GP PDF URLs** for all 5 remaining cities (Grantsville, Bluffdale, Draper, Herriman, Spanish Fork) — update this file and `_18b-2bc_scope.md` with confirmed URLs.
2. **Obtain a production Anthropic API key** (`sk-ant-api03-...`) — OAuth token shares rate limits with the CC session and cannot run Opus 4 for stage 2 CP identification.
3. **Pre-screen each PDF**: visually confirm the FLU map page shows parcel-level detail (individual lots visible), not a regional overview.
4. **Use Anthropic Batch API** for 18b-2c runs (50% cost discount per 18b-2bc_scope.md).

---

## GeoJSON Output Schema (v2 — Phase 18b-2 pipeline-v2)

Each feature in `<city_slug>_gp.geojson` carries the following properties:

| Field | Type | Nullable | Description |
|---|---|---|---|
| `city_slug` | string | no | Machine slug (e.g. `grantsville`) |
| `city_name` | string | no | Human city name |
| `future_layer_type` | string | no | One of the values below |
| `layer_subtype` | string | yes | Free-text label (e.g. "South Valley MPC") |
| `gp_zone_code` | string | yes* | Raw zone code from legend (null for annexation) |
| `gp_zone_description` | string | yes* | Raw zone description (null for annexation) |
| `gp_zone_normalized` | string | yes* | Normalized class (null for annexation) |
| `acreage` | float | yes | Polygon acreage if extractable from source |
| `jurisdiction` | string | no | County jurisdiction slug |
| `source_pdf_url` | string | no | Source PDF URL (per-feature, supports multi-PDF) |
| `source_page_id` | string | no | e.g. `page_2` (0-indexed filtered page) |
| `extraction_method` | string | no | Always `anthropic_vision_claude_opus_4_7_georeferenced` |
| `confidence` | string | no | `anchored_approximation` or `anchored_approximation_yellow` |
| `transform_residual_ft` | float | no | RMSE of affine transform in feet |
| `n_control_points` | int | no | Number of ground-truth control points used |
| `extraction_date` | string | no | ISO date of extraction run |

*\*nullable only when `future_layer_type` is an annexation type (see below).*

### Layer types (`future_layer_type`)

| Value | Description | Zone fields | Scoring signal |
|---|---|---|---|
| `flu` | Future Land Use designation | Required | Primary GP zoning signal |
| `annexation_proposed` | Proposed annexation boundary (not yet annexed) | null | Expansion pressure indicator |
| `annexation_existing_future` | Area within city limits with planned future use | null | Near-term development signal |
| `annexation_existing_city` | Already-annexed area shown for context | null | Current city extent reference |
| `mpc_overlay` | Master Planned Community or special overlay | Optional | High-density/mixed-use signal |
| `other` | Uncategorized GP layer | Optional | Informational only |

### Validation rules

- `future_layer_type` must be one of the six values above.
- `gp_zone_code`, `gp_zone_description`, `gp_zone_normalized` MUST be null when `future_layer_type` is `annexation_proposed`, `annexation_existing_future`, or `annexation_existing_city`.
- `gp_zone_normalized` MUST be one of `GP_ZONE_NORMALIZED_CLASSES` when non-null.
- `source_pdf_url` is per-feature (not per-file) to support multi-PDF aggregated outputs.

### Multi-PDF aggregation

Cities with sectional GPs (e.g. Grantsville) publish multiple PDFs that together form the complete GP. Use `--pdf-urls "url1,url2,url3"` to run the pipeline over all sections and merge features into a single output GeoJSON. Each feature retains its `source_pdf_url` and `source_page_id` for traceability.
