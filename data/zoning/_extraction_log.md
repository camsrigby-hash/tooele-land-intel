# Phase 18b Zoning PDF Vision Extraction Log

This log documents the Phase 18b source discovery and extraction attempt. Repository-state inspection showed that all active jurisdictions should be treated as B1 fallback jurisdictions, because the checked-in Phase 13b-5 scoring output used `prop_class:` source strings and no real per-city zoning source was present. The local execution environment did not expose `ANTHROPIC_API_KEY`, and the available GitHub token could not inspect repository Actions secrets, so the required Anthropic Batch API vision extraction was not submitted. To preserve data integrity, this run wrote valid empty GeoJSON FeatureCollections rather than fabricating polygon coordinates from PDF page/image space.

| City | Source PDF URL | PDF page count | Polygons extracted | Unique zones found | Total LLM cost | Issues |
|---|---|---:|---:|---:|---:|---|
| Grantsville City | https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Central%20Area%20June%202025%20(1).pdf<br>https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Deseret%20Peak%20Area%20June%202025%20(1).pdf<br>https://cms9files.revize.com/grantsvilleut/Document_Center/Department/Community%20&%20Economic%20Development/Zoning%20Map/Zoning%20Map%20Flux%20Area%20June%202025%20(1).pdf | 3 | 0 | 0 | $0.00 | PDF located and downloaded; vision extraction skipped because `ANTHROPIC_API_KEY` was not available locally. No polygons fabricated. |
| Erda City | N/A | 0 | 0 | 0 | $0.00 | Official City Code and Maps page links Zoning Map to an ArcGIS webapp, not a PDF. |
| Tooele City | N/A | 0 | 0 | 0 | $0.00 | City maps page found, but no downloadable official zoning map PDF found in source discovery; available sources appear to be interactive or code PDFs rather than the zoning map. |
| Lehi City | https://www.lehi-ut.gov/media/ywqioi21/lehi_zoning1pdf.pdf | 1 | 0 | 17 | $0.00 | PDF located and downloaded; vision extraction skipped because `ANTHROPIC_API_KEY` was not available locally. No polygons fabricated. |
| Saratoga Springs City | N/A | 0 | 0 | 0 | $0.00 | Planning and Mapping/GIS pages advertise interactive city maps; no official base-zoning PDF found in source discovery. |
| Eagle Mountain City | N/A | 0 | 0 | 0 | $0.00 | Planning/engineering pages and search results point to interactive ArcGIS zoning app/maps; no official base-zoning PDF found. |
| South Jordan City | N/A | 0 | 0 | 0 | $0.00 | FAQ states zoning is available through an interactive zoning map; no official zoning PDF found. |
| Herriman City | N/A | 0 | 0 | 0 | $0.00 | GIS page/search results point to interactive zoning map; no official zoning PDF found. |
| Bluffdale City | N/A | 0 | 0 | 0 | $0.00 | Maps page and search results point to ArcGIS zoning web map and map-order page; no official zoning PDF found. |
| Draper City | N/A | 0 | 0 | 0 | $0.00 | Planning/development map collection is an ArcGIS Experience zoning map; no official zoning PDF found. |
| American Fork City | https://www.americanfork.gov/DocumentCenter/View/4139 | 1 | 0 | 0 | $0.00 | PDF located and downloaded; vision extraction skipped because `ANTHROPIC_API_KEY` was not available locally. No polygons fabricated. |
| Vineyard City | N/A | 0 | 0 | 0 | $0.00 | Planning page/search results point to public ArcGIS GIS maps and zoning feature layer; no official base-zoning PDF found. |
| Spanish Fork City | https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Detailed_Letter.pdf<br>https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/Zoning_Letter.pdf | 2 | 0 | 22 | $0.00 | PDF located and downloaded; vision extraction skipped because `ANTHROPIC_API_KEY` was not available locally. No polygons fabricated. |

## Source-discovery summary

Four jurisdictions had downloadable zoning PDFs located during this run: Grantsville, Lehi, American Fork, and Spanish Fork. The other nine active jurisdictions were flagged because their city pages or search results pointed to interactive ArcGIS/HTML zoning maps, map-order pages, or zoning-code PDFs rather than a published base-zoning map PDF. This follows the instruction to skip HTML-rendered zoning maps rather than scrape them.

## Validation notes

All generated GeoJSON files are syntactically valid FeatureCollections in EPSG:4326, but they intentionally contain zero features because no trusted polygon coordinates were produced. A future rerun with `ANTHROPIC_API_KEY` available should replace these empty collections with closed Polygon features and then validate that every `zone_code` appears in the source PDF legend.

## References

[1]: https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php "Grantsville Zoning Map"
[2]: https://www.lehi-ut.gov/business-development/maps/ "Lehi City Maps"
[3]: https://www.americanfork.gov/276/Planning-Department "American Fork Planning Department"
[4]: https://www.spanishfork.gov/departments/community_development/planning/zoning.php "Spanish Fork Zoning"
[5]: https://erda.gov/city-codes-and-maps/ "Erda City Code and Maps"
[6]: https://www.sjc.utah.gov/FAQ.aspx?QID=193 "South Jordan Planning FAQ"
