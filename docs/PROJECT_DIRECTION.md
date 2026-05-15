# Project Direction — tooele-land-intel

Strategic guide for phase sequencing, data scope, and delivery targets.

---

## Phase Ledger

| Phase | Description | Status | Date |
|---|---|---|---|
| 13b-1–8 | Pipeline foundation: parcel ingestion, geocoding, AADT scoring, zoning score, census ACS join, commute corridor, vacancy class | Shipped | 2026-04 |
| 18b-1 | Current zoning REST extraction — 13/13 cities, Saratoga Springs hotfix included | Shipped | 2026-05-11 |
| 18b-2a | GP FLU REST extraction — 7/13 cities (Vineyard added via Experience recheck); 6 cities remain for PDF path | Shipped | 2026-05-11 |
| 18b-2b | GP FLU PDF pipeline — `scripts/gp_pdf_extract.py` (8-stage pipeline). Erda validation: RMSE 4664 ft, 0 features (regional-overview map). Pipeline verified end-to-end. | **Shipped** | 2026-05-14 |
| 18b-2c | GP FLU PDF rollout — 5 cities (Grantsville, Bluffdale, Draper, Herriman, Spanish Fork). Prerequisites: PDF URLs confirmed, production API key, pre-screen maps | **Next** | — |
| 18b-3 | D1 load: zoning taxonomy normalization, Lehi remapping, Tooele multi-zone handling, schema migration `0006_gp_zoning.sql` | Queued | — |

---

## Data Scope

**Active jurisdictions (13):** Erda, Grantsville, Tooele City, Lehi, Saratoga Springs, Eagle Mountain, South Jordan, Herriman, Bluffdale, Draper, American Fork, Vineyard, Spanish Fork.

**Current zoning coverage:** 13/13 cities have valid GeoJSONs in `data/zoning/current/`. Saratoga Springs UT hotfix merged 2026-05-14.

**GP FLU coverage:** 7/13 cities have REST-sourced GeoJSONs in `data/zoning/future/`. 6 cities remain on PDF path (18b-2c): Erda (pipeline ran, regional-overview map — 0 features, marked `gp_data: regional_map_only`), Grantsville, Bluffdale, Draper, Herriman, Spanish Fork. See `_quality_review.md`.

---

## Architecture Notes

- All zoning and GP GeoJSONs use `extraction_method: arcgis_rest` or `extraction_method: pdf_georef` and `confidence` field for D1 ingest routing.
- D1 migration `0006_gp_zoning.sql` will add `gp_zone_code`, `gp_zone_normalized`, `gp_zone_description`, `source_authority` columns.
- NLS_LandUseService data (Lehi, Eagle Mountain, Saratoga Springs GP) carries `source_authority: unverified_regional_study` until verified against city-adopted GP maps.
- Tooele City GP has comma-separated multi-zone codes — 18b-3 must implement Option B (compatible_zones array) or Option A (split at comma).

---

## Strategic Decisions Log

### SD-16 — Bounding box constraint for REST extraction (2026-05-11)

Any ArcGIS REST query targeting a Utah jurisdiction must include an explicit Utah bounding box geometry constraint to prevent name-collision extractions. This was surfaced during 18b-1 sanity checks when `saratoga_springs_ut_zoning.geojson` was found to contain Saratoga Springs, NY features — same city name, different state, wrong ArcGIS org (`M7jfYoTaLM0yE75d`).

**Required constraint parameters:**
```
&geometry={"xmin":-114,"ymin":37,"xmax":-109,"ymax":42,"spatialReference":{"wkid":4326}}
&geometryType=esriGeometryEnvelope
&inSR=4326
```

**Full pattern:**
```
?where=1=1&outFields=*&outSR=4326&f=geojson&geometry={"xmin":-114,"ymin":37,"xmax":-109,"ymax":42,"spatialReference":{"wkid":4326}}&geometryType=esriGeometryEnvelope&inSR=4326
```

Apply to all future REST extractions before committing any GeoJSON to `data/zoning/`.
