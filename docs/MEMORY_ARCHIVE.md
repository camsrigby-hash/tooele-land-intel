# Memory Archive — tooele-land-intel

Archived research findings and superseded phase content. Do not edit; append only.

---

## Phase 18b first-attempt research findings

**Archived**: 2026-05-11  
**Source**: phase-18b-zoning-extraction branch (Manus extraction attempt, branch deleted prior to 2026-05-11 verification session)  
**Status**: Branch was deleted; bad GeoJSON artifacts were not preserved. Research findings below are reconstructed from the 18b-1 and 18b-2a inventory files and the 18b-2b/c scope document.

### What the first attempt established

The phase-18b-zoning-extraction attempt (Manus) identified the core source landscape for the 13-city GP FLU extraction:

**Cities with public ArcGIS REST FLU layers (later successfully extracted in 18b-2a):**
- South Jordan: `gis2.southjordanutah.gov/server/rest/services/Development/LandUse/MapServer/2` (Future Land Use 2020, 155 features)
- Tooele City: `services3.arcgis.com/3PP5uLqByhZNekjG/arcgis/rest/services/LandUse/FeatureServer/0` (63 features)
- American Fork: `maps.afcity.org/arcgis/rest/services/Planning/Land_Use2/MapServer/0` (97 features)
- Lehi, Eagle Mountain, Saratoga Springs: North Lakeshore Study service `services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer` (layers 2, 4, 0 respectively)

**Cities confirmed PDF-only path:**
- Erda: No city GIS portal; GP PDF at `erda.gov/wp-content/uploads/2022/08/Erda-General-Plan_2022-06-23.pdf` (2022)
- Grantsville: 3-part Jan 2020 FLU map series in Document Center at grantsvilleut.gov
- Bluffdale: GP PDF at `bluffdale.gov/DocumentCenter/View/5049/Bluffdale-General-Plan-PDF`
- Vineyard: FLU map PDF at vineyardutah.gov; ArcGIS Experience map may have REST endpoint
- Draper: `gis.hlplanning.com` REST root checked; no Draper FLU/GP REST layer identified
- Herriman: Official GIS page lists GP map but no public queryable REST endpoint
- Spanish Fork: `suvgis.spanishfork.org` and download page checked; no FLU REST layer found

### Why the first attempt's GeoJSONs were discarded

The first-attempt GeoJSONs (from the phase-18b-zoning-extraction branch) were discarded because they failed geometry validation — likely bad ring geometry, wrong projection, or wrong-jurisdiction sources. The branch was deleted and a clean re-extraction was performed in phases 18b-1 (current zoning) and 18b-2a (GP FLU REST).

### Lessons applied in 18b-1 / 18b-2a

1. **Verify jurisdiction by coordinate**: Always check that extracted coordinates fall in Utah EPSG:4326 range (lng [-114,-109], lat [37,42]) before committing.
2. **Saratoga Springs name collision**: ArcGIS contains layers for both Saratoga Springs UT and Saratoga Springs NY. The org ID `M7jfYoTaLM0yE75d` is the NY city — discard. Use `gis.saratogaspringscity.com` for UT.
3. **NLS_LandUseService authority**: The North Lakeshore Study service (`pA2nEVnB6tquxgOW`) is a regional planning study, not a city-adopted GP. Acceptable for MVP signal grade but must be flagged in D1 as `source_authority: unverified_regional_study`.
4. **ArcGIS ring artifacts**: REST exports commonly have self-intersecting rings. Apply `buffer(0)` before `unary_union`; do not treat these as wrong-jurisdiction failures.
