# Phase 18b-2a — Source authority caveats

Issues identified during post-extraction review that must be resolved before 18b-3 D1 load.

---

## 1. NLS_LandUseService — source authority uncertain for Lehi, Eagle Mountain, Saratoga Springs

**Affected GeoJSONs**: `lehi_gp.geojson`, `eagle_mountain_gp.geojson`, `saratoga_springs_gp.geojson`

**Source**: All three use `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer` (North Lakeshore Study regional service, hosted by unknown ArcGIS Online organization `pA2nEVnB6tquxgOW`).

**Concern**: NLS = "North Lakeshore Study" is a regional planning study covering the north shore of Utah Lake. It is **not** necessarily an officially-adopted city General Plan or city-maintained layer. The Lehi layer is named "Lehi General Plan" and the data appears city-level, but it is sourced from a regional study, not from a city-published GIS service. This introduces two risks:
- **Currency**: The data may not reflect the most recently adopted GP amendments (Lehi's GP has been updated multiple times through 2022+).
- **Authority**: A regional study layer may not have the same legal weight as the city's own adopted General Plan map.

**Action required before 18b-3**:
1. Verify whether the NLS_LandUseService data matches Lehi's, Eagle Mountain's, and Saratoga Springs' most recently adopted GP FLU maps. Cross-reference against the GP documents linked on each city's planning page.
2. If the NLS data is stale or divergent, these cities should be moved to the PDF path (18b-2c) or contacted for a direct shapefile/FLU download.
3. Until verified, mark these cities with `source_authority: unverified_regional_study` in D1 rather than `source_authority: city_adopted_gp`.

**How to verify**: Load each city's GeoJSON in QGIS alongside the city's published GP land use map PDF. Check whether major land use boundaries match. Focus on areas with known recent growth/rezoning (Lehi Technology Corridor, Eagle Mountain western expansion).

---

## 2. Tooele City — multi-zone comma-separated codes (normalization edge case)

**Affected GeoJSON**: `tooele_city_gp.geojson`

**Issue**: 37 of 63 features have comma-separated `gp_zone_code` values, e.g.:
- `"MR-25, MR-16"` (multiple residential density designations)
- `"RC, RD"` (commercial + residential split)
- `"MU-G, MU-B"` (two mixed-use types)

These are single polygons that span multiple zone designations per the source layer. The source ArcGIS service stores them as a single string, not as separate features.

**Decision required for 18b-3** (two options):

**Option A — Split at comma**: Parse `gp_zone_code` on comma, create one feature per zone code, keep identical geometry. Pro: STRtree join assigns one zone per parcel. Con: inflates feature count, may create duplicate parcel assignments for parcels fully inside the multi-zone polygon.

**Option B — Store as `compatible_zones` array**: Keep the polygon as-is but store all zone codes in a JSON array column `gp_compatible_zones` and set `gp_zone_code` to the primary (first) code. Pro: preserves source fidelity. Con: requires schema change to support array column in D1 migration `0006_gp_zoning.sql`.

**Recommendation**: Option B (compatible_zones array). The multi-zone polygons are likely transition zones or areas where the GP allows more than one compatible use. Splitting the geometry creates artificial duplicates that will confuse the spread signal. The D1 migration can store the array as a JSON string column.

**Normalization impact**: The following multi-zone codes appear in Tooele City and need normalized mappings in `gp_taxonomy.yaml` (18b-2d task):
- `MR-25`, `MR-16`, `MR-7` — residential density tiers
- `RC`, `RD` — commercial/residential combination
- `MU-G`, `MU-B` — mixed-use variants
- `LC` — likely light commercial
- Other codes visible in `tooele_city_gp.geojson` properties

18b-2d must handle these as individual tokens (split on comma, normalize each, store normalized array alongside `gp_zone_normalized`).
