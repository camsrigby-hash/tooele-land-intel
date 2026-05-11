# Project Direction — tooele-land-intel

Strategic guide for phase sequencing, data scope, and delivery targets.

---

## Phase Ledger

| Phase | Description | Status | Date |
|---|---|---|---|
| 13b-1–8 | Pipeline foundation: parcel ingestion, geocoding, AADT scoring, zoning score, census ACS join, commute corridor, vacancy class | Shipped | 2026-04 |
| 18b-1 | Current zoning REST extraction — 13 cities (12 good + Saratoga Springs hotfix pending) | Shipped ⚠️ | 2026-05-11 |
| 18b-2a | GP future land use REST extraction — 6 cities (all PASS/WARN, NLS authority caveat flagged) | Shipped | 2026-05-11 |
| 18b-2b | GP FLU PDF pipeline — Erda prototype (`scripts/gp_pdf_extract.py`) | **Next** | — |
| 18b-2c | GP FLU PDF rollout — remaining 6 cities (Grantsville, Bluffdale, Vineyard, Draper, Herriman, Spanish Fork) | Queued | — |
| 18b-3 | D1 load: zoning taxonomy normalization, Lehi remapping, Tooele multi-zone handling, schema migration `0006_gp_zoning.sql` | Queued | — |

### 18b-1 known issue
`saratoga_springs_ut_zoning.geojson` was extracted from Saratoga Springs, NY (wrong city, same name). File removed from main on 2026-05-11. Re-extraction from `gis.saratogaspringscity.com` required before D1 load. Track as **18b-1 hotfix** — can run alongside 18b-2b/c.

---

## Data Scope

**Active jurisdictions (13):** Erda, Grantsville, Tooele City, Lehi, Saratoga Springs, Eagle Mountain, South Jordan, Herriman, Bluffdale, Draper, American Fork, Vineyard, Spanish Fork.

**Current zoning coverage:** 12/13 cities have valid GeoJSONs in `data/zoning/current/`. Saratoga Springs UT pending re-extraction.

**GP FLU coverage:** 6/13 cities have REST-sourced GeoJSONs in `data/zoning/future/`. 7 cities remain on PDF path (18b-2b/c).

---

## Architecture Notes

- All zoning and GP GeoJSONs use `extraction_method: arcgis_rest` or `extraction_method: pdf_georef` and `confidence` field for D1 ingest routing.
- D1 migration `0006_gp_zoning.sql` will add `gp_zone_code`, `gp_zone_normalized`, `gp_zone_description`, `source_authority` columns.
- NLS_LandUseService data (Lehi, Eagle Mountain, Saratoga Springs GP) carries `source_authority: unverified_regional_study` until verified against city-adopted GP maps.
- Tooele City GP has comma-separated multi-zone codes — 18b-3 must implement Option B (compatible_zones array) or Option A (split at comma).
