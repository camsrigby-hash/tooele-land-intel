# Data Dictionary — tooele-land-intel

This document describes the schema of all data artifacts produced by the tooele-land-intel pipeline.

---

### census_acs_blockgroups.csv

**Source:** Census ACS 5-year API (`B19013_001E`) + TIGERweb Tracts_Blocks MapServer (Layer 5, ACS 2024 Block Groups)
**Update Frequency:** Annual (Census ACS December release)
**Scope:** 7 Utah counties — Tooele (045), Salt Lake (035), Utah (049), Davis (011), Weber (057), Wasatch (051), Box Elder (003)
**Purpose:** Median household income at block-group level. Used by Phase 13b-6b for spatial join to parcel centroids to populate `parcel_records.median_income`.

| Column | Type | Description |
|---|---|---|
| `geoid` | string | 12-digit Census GEOID (`state[2] + county[3] + tract[6] + block_group[1]`). Primary key. |
| `state_fips` | string | 2-digit state FIPS code (`'49'` for Utah). |
| `county_fips` | string | 3-digit county FIPS code (e.g., `'035'` for Salt Lake). |
| `tract` | string | 6-digit Census tract code. |
| `block_group` | string | 1-digit Census block group code. |
| `median_income` | integer | Median household income in past 12 months (inflation-adjusted 2023 dollars). Null if Census suppressed the value (sentinel `-666666666` treated as NULL). |
| `boundary_geojson` | string | GeoJSON Polygon/MultiPolygon geometry of the block group boundary (from TIGERweb Layer 5, EPSG:4326). |
| `fetched_at` | timestamp | UTC ISO-8601 timestamp of when the data was fetched. |

**Row counts (2023 ACS 5-year, fetched 2026-05-06):**

| County | FIPS | Block Groups |
|---|---|---|
| Salt Lake | 035 | 712 |
| Utah | 049 | 434 |
| Davis | 011 | 193 |
| Weber | 057 | 166 |
| Box Elder | 003 | 42 |
| Tooele | 045 | 39 |
| Wasatch | 051 | 22 |
| **Total** | | **1,608** |

**Coverage:** 96.6% median income populated (1,554 / 1,608). 100% boundary GeoJSON populated.
