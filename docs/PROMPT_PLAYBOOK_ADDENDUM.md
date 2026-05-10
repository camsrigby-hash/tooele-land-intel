# Prompt Playbook Addendum

## Phase 18b

## EXTRACTION COMPLETE — awaiting CC normalization/D1 load

Date: 2026-05-10

Branch: `phase-18b-zoning-extraction`

Pull request: https://github.com/camsrigby-hash/tooele-land-intel/pull/8

Phase 18b repo-state review found that all 13 active jurisdictions should be treated as B1 fallback jurisdictions because the checked-in Phase 13b-5 scoring output used `prop_class:` source strings and no real per-city zoning source was present. Deliverables were finalized under `data/zoning/`, including 13 valid per-city GeoJSON FeatureCollections, `_taxonomy_proposal.md`, `_extraction_log.md`, source PDFs for cities where downloadable PDFs were found, and Batch API audit/support files.

| Metric | Result |
|---|---:|
| B1 fallback jurisdictions in scope | 13 |
| Cities with downloadable zoning PDFs processed | 4 |
| Cities skipped because only interactive/HTML or non-map sources were found | 9 |
| Polygon features extracted | 54 |
| Unique extracted city/zone-code pairs | 45 |
| Batch API input tokens | 27,766 |
| Batch API output tokens | 13,767 |
| Total estimated LLM cost incurred | $0.7245 |

Anthropic Messages Batch API vision extraction was completed with `claude-opus-4-7` for the official downloadable zoning PDFs located for Grantsville, Lehi, American Fork, and Spanish Fork. The extracted polygons are written into the corresponding city GeoJSON files with EPSG:4326 coordinates and source/quality metadata. The model reported low extraction quality because the PDF maps lacked reliable georeferencing grids; therefore, the geometry should be treated as planning-intelligence approximation pending CC normalization and any future authoritative GIS replacement.
