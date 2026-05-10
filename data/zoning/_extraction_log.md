# Phase 18b Zoning PDF Vision Extraction Log

This log documents the completed Phase 18b source discovery and Anthropic Batch API vision extraction. Repository-state inspection showed that all active jurisdictions should be treated as B1 fallback jurisdictions because the checked-in Phase 13b-5 scoring output used `prop_class:` source strings and no real per-city zoning source was present. Four jurisdictions had official downloadable zoning PDF sources; those pages were rendered to images and submitted to `claude-opus-4-7` via the Messages Batch API. Nine jurisdictions were skipped because only interactive/HTML or non-map sources were found.

| City | Source PDF URL | PDF page count | Polygons extracted | Unique zones found | Total LLM cost | Issues |
|---|---|---:|---:|---:|---:|---|
| Grantsville City | https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Central%20Area%20June%202025%20(1).pdf<br>https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Deseret%20Peak%20Area%20June%202025%20(1).pdf<br>https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Flux%20Area%20June%202025%20(1).pdf | 3 | 16 | 11 | $0.21 | Batch vision extraction completed. Coordinates are simplified/low-confidence where source maps lacked explicit georeferencing; see GeoJSON metadata page notes. |
| Erda City | N/A | 0 | 0 | 0 | $0.00 | Official City Code and Maps page links zoning to an ArcGIS webapp, not a PDF. |
| Tooele City | N/A | 0 | 0 | 0 | $0.00 | No downloadable official zoning map PDF found; available sources were interactive/code documents rather than base-zoning PDF maps. |
| Lehi City | https://www.lehi-ut.gov/media/ywqioi21/lehi_zoning1pdf.pdf | 1 | 8 | 8 | $0.11 | Batch vision extraction completed. Coordinates are simplified/low-confidence where source maps lacked explicit georeferencing; see GeoJSON metadata page notes. |
| Saratoga Springs City | N/A | 0 | 0 | 0 | $0.00 | Planning and GIS pages advertise interactive city maps; no official base-zoning PDF found. |
| Eagle Mountain City | N/A | 0 | 0 | 0 | $0.00 | Planning/engineering sources point to interactive ArcGIS zoning apps/maps; no official base-zoning PDF found. |
| South Jordan City | N/A | 0 | 0 | 0 | $0.00 | FAQ states zoning is available through an interactive zoning map; no official zoning PDF found. |
| Herriman City | N/A | 0 | 0 | 0 | $0.00 | GIS/search sources point to interactive zoning maps; no official zoning PDF found. |
| Bluffdale City | N/A | 0 | 0 | 0 | $0.00 | Maps/search sources point to ArcGIS zoning web map and map-order page; no official zoning PDF found. |
| Draper City | N/A | 0 | 0 | 0 | $0.00 | Planning/development map collection is an ArcGIS Experience zoning map; no official zoning PDF found. |
| American Fork City | https://www.americanfork.gov/DocumentCenter/View/4139 | 1 | 16 | 16 | $0.21 | Batch vision extraction completed. Coordinates are simplified/low-confidence where source maps lacked explicit georeferencing; see GeoJSON metadata page notes. |
| Vineyard City | N/A | 0 | 0 | 0 | $0.00 | Planning/search sources point to public ArcGIS GIS maps and zoning feature layers; no official base-zoning PDF found. |
| Spanish Fork City | https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Detailed_Letter.pdf<br>https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Letter.pdf | 2 | 14 | 10 | $0.19 | Batch vision extraction completed. Coordinates are simplified/low-confidence where source maps lacked explicit georeferencing; see GeoJSON metadata page notes. |

## Batch API usage and cost

The successful Batch run used **27,766 input tokens** and **13,767 output tokens**. Applying the Batch API 50% discount to Opus-family unit rates gives an estimated LLM cost of **$0.7245**, which is below the **$15.00** ceiling. A first validation-only submission errored because `temperature` is deprecated for `claude-opus-4-7`; that run returned no successful messages and no extracted outputs.

## Source-discovery summary

Four jurisdictions had downloadable zoning PDFs located and processed: Grantsville, Lehi, American Fork, and Spanish Fork. The other nine active jurisdictions were skipped because their city pages or search results pointed to interactive ArcGIS/HTML zoning maps, map-order pages, or zoning-code PDFs rather than a published base-zoning map PDF. This follows the Phase 18b instruction to skip HTML-rendered zoning maps rather than scrape them.

## Validation notes

All 13 per-city GeoJSON files remain syntactically valid EPSG:4326 FeatureCollections. The four PDF-backed cities now contain extracted polygon features; skipped cities remain empty FeatureCollections with metadata explaining the absence of an official PDF source. The extracted geometry should be treated as **planning-intelligence approximation**, not survey-grade or ordinance-grade zoning geometry, because the PDF maps did not expose machine-readable geospatial vectors or reliable coordinate grids to the vision model.

## Per-page extraction notes

### Grantsville City

> grantsville_2: low — Map shows Deseret Peak area east/southeast of Grantsville. Extracted approximate large-area polygons for major zones (MG, CG, MU) where geographic placement could be inferred from named roads (Hwy 138, Erda Way, Sheep Ln, Hwy 112, Depot Boundary Rd). Smaller residential parcels and PUD overlays omitted due to insufficient georeferencing detail.

> grantsville_1: low — Map shows Grantsville central area zoning. Extracted approximate generalized polygons for major zone areas based on Main St (HWY 138), Hwy 112, and rough city extents. Detail is highly simplified; many small parcels omitted.

> grantsville_3: low — Flux Area zoning map northwest of Grantsville. Limited well-known street references for precise georeferencing; approximate simplified polygons provided for the dominant zones (MG north area, MU central, R-1-21 SE, PUD east) based on Hwy 138 / Lincoln Hwy alignment and Grantsville city location.

### Lehi City

> lehi_1: low — Lehi zoning map is highly complex with hundreds of small parcels. Extracted only large, clearly identifiable zone areas as simplified polygons based on approximate geographic placement using known Lehi landmarks (I-15, Utah Lake, Thanksgiving Point area). Many smaller zones omitted to avoid invented geometry.

### American Fork City

> american_fork_1: low — Extracted approximate simplified polygons for major zones in American Fork City based on visible labels and street references. Many small parcels and detailed boundaries omitted due to map complexity. Coordinates approximated using known American Fork geography (Utah Lake to south, I-15 corridor, State Street).

### Spanish Fork City

> spanish_fork_2: low — Map lacks coordinate grid and named street labels visible at this resolution. Provided coarse approximate polygons for the largest, most identifiable zone areas (I-1 industrial in NW, R-1-6 core, R-1-9/R-1-12 outer residential, R-R rural residential SW, I-3 heavy industrial SE) based on the city's known footprint around 40.115N, -111.65W. Smaller districts omitted to avoid invented geometry.

> spanish_fork_1: low — Map lacks explicit coordinate grid; only approximate large-scale zone areas extracted based on Spanish Fork's known geography (centered near 40.115N, -111.654W). Many small parcels omitted due to inability to geographically place reliably.

## References

[1]: https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php "Grantsville Zoning Map"
[2]: https://www.lehi-ut.gov/business-development/maps/ "Lehi City Maps"
[3]: https://www.americanfork.gov/276/Planning-Department "American Fork Planning Department"
[4]: https://www.spanishfork.gov/departments/community_development/planning/zoning.php "Spanish Fork Zoning"
[5]: https://erda.gov/city-codes-and-maps/ "Erda City Code and Maps"
[6]: https://www.sjc.utah.gov/FAQ.aspx?QID=193 "South Jordan Planning FAQ"
