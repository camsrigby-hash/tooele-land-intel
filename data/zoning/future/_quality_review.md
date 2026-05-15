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
