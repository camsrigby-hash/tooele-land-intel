# Phase 18b-2a REST Future Land Use Inventory

**extraction_date: 2026-05-11**

This inventory records the REST-only future land use and general plan layer checks performed for the Phase 18b-2a extraction. Each extracted layer was queried through public ArcGIS REST, requested with `outSR=4326`, and normalized to the required property schema: `city_slug`, `city_name`, `gp_zone_code`, `gp_zone_description`, `jurisdiction`, `source_rest_url`, `extraction_method='arcgis_rest'`, and `confidence='rest_api'`.

| City | REST status | Output | Feature count | Source layer |
|---|---:|---|---:|---|
| South Jordan | Found and extracted | `south_jordan_gp.geojson` | 155 | `https://gis2.southjordanutah.gov/server/rest/services/Development/LandUse/MapServer/2` |
| Lehi | Found and extracted | `lehi_gp.geojson` | 284 | `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer/2` |
| Eagle Mountain | Found and extracted | `eagle_mountain_gp.geojson` | 972 | `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer/4` |
| Saratoga Springs | Found and extracted | `saratoga_springs_gp.geojson` | 217 | `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer/0` |
| American Fork | Found and extracted | `american_fork_gp.geojson` | 97 | `https://maps.afcity.org/arcgis/rest/services/Planning/Land_Use2/MapServer/0` |
| Tooele City | Found and extracted | `tooele_city_gp.geojson` | 63 | `https://services3.arcgis.com/3PP5uLqByhZNekjG/arcgis/rest/services/LandUse/FeatureServer/0` |
| Draper | No FLU REST layer found | None | 0 | PDF path |
| Herriman | No queryable FLU REST layer found | None | 0 | PDF path |
| Spanish Fork | No FLU REST layer found | None | 0 | PDF path |

## City-by-city notes

**South Jordan.** The requested source `https://gis.sjc.utah.gov/sjcmaps/rest/services/CarteTEST/CGTEST_LANDBASE/FeatureServer` was checked first, but the host did not resolve from the sandbox. Alternate South Jordan GIS paths under `gis2.southjordanutah.gov/sjcmaps` returned an access-denied response, and `gis2.southjordanutah.gov/server/rest/services/CarteTEST/CGTEST_LANDBASE/FeatureServer` returned no public layer metadata. A public South Jordan REST service, `https://gis2.southjordanutah.gov/server/rest/services/Development/LandUse/MapServer`, exposed a polygon layer named **Future Land Use 2020** at layer `2`; this layer includes `LANDUSE_ID` and `DESCRIPTION` fields and was extracted to `south_jordan_gp.geojson`. The source URL in the output points to the accessible public layer actually queried.

**Lehi.** The page `https://www.engagelehi.org/general-plan-update/places/future-land-use-map` was inspected in the browser and after the map modal was opened; the Engage runtime exposed project marker API calls and ArcGIS basemap/vector tile resources, but no public ArcGIS FeatureServer or MapServer for the thematic polygons. ArcGIS Online search located the North Lakeshore Study land use service at `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer`; layer `2`, **Lehi General Plan**, is a polygon feature layer with `Code` and `Descriptio` fields plus renderer labels such as `A - Agricultural` and `LDR - Low Density Residential`. Layer `2` was extracted to `lehi_gp.geojson`, with renderer labels used to normalize Lehi alphanumeric GP codes.

**Eagle Mountain.** The official engineering/mapping page `https://eaglemountain.gov/government/engineering-mapping/` links to the Eagle Mountain ArcGIS organization; organization-owned candidates found there were current zoning or transportation layers, not a distinct future land use/general plan layer. The North Lakeshore Study service exposed layer `4`, **Eagle Mnt. Land Use**, at `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer/4`; this layer contains `LANDUSEDES` values such as Foothill Residential, Neighborhood Residential, and Regional Commercial. It was extracted to `eagle_mountain_gp.geojson`.

**Saratoga Springs.** The official mapping page `https://saratogasprings-ut.gov/210/MappingGIS` was checked, including its public HTML and linked GIS/Master Plans references; no city-hosted public FLU FeatureServer or MapServer was found. The North Lakeshore Study service exposed layer `0`, **Saratoga Springs Land Use**, at `https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services/NLS_LandUseService/FeatureServer/0`. That layer was extracted to `saratoga_springs_gp.geojson`; the service reported 218 records, and 217 polygon features with geometry were written.

**Herriman.** The official page `https://www.herriman.gov/gis` was checked and it lists a **General Plan Map** described as projected future development of the city. A direct browser click timed out, and parsing the saved HTML did not reveal a public ArcGIS FeatureServer or MapServer endpoint for a queryable future land use layer. Because no REST layer was found, Herriman remains on the PDF/manual path.

**Draper.** The provided REST root `https://gis.hlplanning.com/server/rest/services` was checked for Draper, planning, general plan, future land use, and land use candidates. No distinct public Draper future land use/general plan FeatureServer or MapServer layer was identified, and current zoning was intentionally excluded because it belongs to Phase 18b-1. Draper remains on the PDF/manual path.

**American Fork.** The official mapping page `https://americanfork.gov/841/Mapping-GIS` and `https://maps.afcity.org/arcgis/rest/services` roots were checked. The REST service `https://maps.afcity.org/arcgis/rest/services/Planning/Land_Use2/MapServer/0` exposes a polygon layer named **Land Use** with `LAYER` values such as Design Commercial, Planned Community, Residential Medium Density, and Public Parks & Open Space. This is distinct from the current-zoning PDF path and was extracted to `american_fork_gp.geojson`. A smaller TOD summary service was also observed but was not used as the citywide GP FLU source.

**Spanish Fork.** The sources `https://suvgis.spanishfork.org` and `https://www.spanishfork.gov/departments/public_works/download_map_data.php` were checked for public REST services or downloadable map-data references exposing a future land use/general plan polygon layer. No distinct public ArcGIS REST FLU layer was found during this REST-only pass, and current zoning was excluded. Spanish Fork remains on the PDF/manual path.

**Tooele City.** The requested web application `https://tooelecitygis.maps.arcgis.com/apps/webappviewer/index.html?id=ea1fc0fb757a454cae04dd1c36403c60` was inspected through its ArcGIS Online item data. Its backing web map `6f1f4e0c6694494fa8227d04a641784f` exposes separate `LandUse` and `Zoning` services, confirming that `https://services3.arcgis.com/3PP5uLqByhZNekjG/arcgis/rest/services/LandUse/FeatureServer/0` is a distinct future/general land use layer rather than current zoning. The layer includes `LandUseCod` and `LandUseTyp` fields and was extracted to `tooele_city_gp.geojson`.

## Cities without a public FLU REST layer

PDF path — CC will handle in 18b-2b/c:

| City | Reason |
|---|---|
| Draper | Checked the provided `gis.hlplanning.com` REST root and planning/land-use candidates; no Draper FLU/general-plan REST layer was identified. |
| Herriman | Official GIS page lists a General Plan Map, but no public queryable REST endpoint was exposed by the page or its parsed HTML. |
| Spanish Fork | Checked `suvgis.spanishfork.org` and the city download-map-data page; no distinct public FLU/general-plan REST layer was identified. |
