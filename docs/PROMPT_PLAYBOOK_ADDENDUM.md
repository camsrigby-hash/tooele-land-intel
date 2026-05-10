# Prompt Playbook Addendum

## Phase 18b

## EXTRACTION COMPLETE — awaiting CC normalization/D1 load

Date: 2026-05-10

Branch: `phase-18b-zoning-extraction`

Pull request: https://github.com/camsrigby-hash/tooele-land-intel/pull/8

Phase 18b repo-state review found that all 13 active jurisdictions should be treated as B1 fallback jurisdictions because the checked-in Phase 13b-5 scoring output used `prop_class:` source strings and no real per-city zoning source was present. Deliverables were added under `data/zoning/`, including 13 valid per-city GeoJSON FeatureCollections, `_taxonomy_proposal.md`, `_extraction_log.md`, source PDFs for cities where downloadable PDFs were found, and source-discovery support files.

| Metric | Result |
|---|---:|
| B1 fallback jurisdictions in scope | 13 |
| Cities with downloadable zoning PDFs located | 4 |
| Cities skipped because only interactive/HTML or non-map sources were found | 9 |
| Polygon features extracted | 0 |
| Total LLM cost incurred | $0.00 |

No Anthropic Batch API vision calls were submitted because the local task environment did not expose `ANTHROPIC_API_KEY`, and the available GitHub token could not inspect Actions secrets. To preserve data integrity, the run did not fabricate polygon coordinates from PDF page/image coordinates; all GeoJSON files are valid empty FeatureCollections pending a rerun in an environment with the required Anthropic secret.
