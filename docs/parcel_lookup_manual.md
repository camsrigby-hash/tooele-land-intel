# Tooele Land Intel — Parcel Lookup Manual

## Quick start

```bash
# Install deps
pip install -r requirements.txt

# Look up a parcel
python scripts/lookup_parcel.py 01-440-0-0019 --pretty
```

## What gets returned

| Field | Source | Notes |
|-------|--------|-------|
| `owner` | Tooele County Assessor | Primary owner name |
| `all_owners` | Tooele County Assessor | All owners as string |
| `acres` | Assessor tax acreage | May differ slightly from `acres_geo` |
| `acres_geo` | GIS computed acreage | More precise for physical area |
| `situs_address` | Assessor | Property street address |
| `area_name` | Assessor | Subdivision or area name |
| `section_twp_range` | Assessor | PLSS location |
| `total_market_value` | Assessor | County market value estimate |
| `property_codes` | Assessor | See Greenbelt / class codes below |
| `jurisdiction` | Spatial intersect | Which city/county governs this parcel |
| `zoning.zone_code` | County GIS zoning layer | Current zoning designation |
| `zoning.description` | County GIS | Human-readable zone name |
| `general_plan.name` | County 2022 General Plan | Future land use designation |
| `centroid_lon_lat` | Computed | WGS84 lon/lat of parcel center |

## Property codes
Tooele County uses multi-character property codes on the assessor record:
- `G` prefix = Greenbelt agricultural deferral (Utah Code 59-2-503)
  - **Important:** Greenbelt status means the land is taxed at agricultural value, which is far below market. Rollback taxes (up to 5 years) apply if the land is converted.
- `Z` = Class 2 agricultural
- Numbers (4, 5, etc.) = land type class codes

## Parcel ID format
Tooele County parcel IDs follow the pattern: `XX-XXX-X-XXXX`
- First segment: township/range group
- Second: section
- Third: quarter
- Fourth: parcel sequence

## Finding a parcel ID
1. Tooele County Assessor: https://eagleweb.tooeleco.gov/assessor/
2. ArcGIS Online map: https://tooelecountygis.maps.arcgis.com/
3. Property tax records portal

## Running via GitHub Actions (cloud)
1. Go to: https://github.com/camsrigby-hash/tooele-land-intel/actions
2. Click "Parcel Lookup" in the left sidebar
3. Click "Run workflow"
4. Enter the parcel ID and click "Run workflow"
5. The JSON result is printed in the job log and saved as a downloadable artifact

## Limitations and caveats
- Zoning data comes from the county GIS layer — verify with the relevant city if the parcel is within Erda or Grantsville city limits.
- Erda City incorporated in 2021; GIS layers may still show old county zoning for some Erda parcels.
- The General Plan layer is the 2022 plan — amendments may have occurred since then.
- Greenbelt property codes indicate agricultural tax status, not an easement or legal restriction on development — consult a land attorney for conversion feasibility.
- Market values are county assessor estimates, not appraisals.
