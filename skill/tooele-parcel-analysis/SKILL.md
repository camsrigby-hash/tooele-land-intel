# Skill: Tooele Parcel Analysis

## What this skill does
Given a Tooele County, Utah parcel ID, this skill:
1. Fetches parcel attributes (owner, acreage, address) from the county ArcGIS REST service
2. Intersects the parcel centroid against the zoning and General Plan layers
3. Summarizes jurisdiction, current zoning, future land use designation, and market value
4. Identifies nearby agenda activity from planning commission / city council records
5. Suggests highest-and-best-use scenarios based on the combined data

## How to invoke
You must have a Tooele County parcel ID in the format `XX-XXX-X-XXXX` (e.g. `01-440-0-0019`).

**If running locally:**
```bash
python scripts/lookup_parcel.py 01-440-0-0019 --pretty
```

**If running via GitHub Actions:**
Go to the repo Actions tab → "Parcel Lookup" → Run workflow → enter parcel ID.

## ArcGIS endpoints used
See `references/arcgis_endpoints.md` for the full list of live service URLs, field names, and layer IDs.

## Jurisdiction reference
See `references/jurisdictions.md` for zoning code glossaries, General Plan categories, and planning commission meeting schedules.

## Output schema
```json
{
  "parcel_id": "01-440-0-0019",
  "owner": "TITMUS SUNNIE JT",
  "all_owners": "TITMUS SUNNIE JT, TITMUS JANAE H JT",
  "acres": 12.97,
  "acres_geo": 13.09,
  "situs_address": "...",
  "area_name": "ERDA WAY",
  "section_twp_range": "S 34 T 2S R 5W",
  "subdivision": null,
  "year_built": null,
  "total_market_value": 648500,
  "property_codes": "GZ4",
  "jurisdiction": "Erda City",
  "zoning": {
    "zone_code": "A-1",
    "description": "Agricultural",
    "jurisdiction_layer": "erda",
    "jurisdiction": "Erda",
    "landuse_code": "...",
    "ordinance": "..."
  },
  "general_plan": {
    "landuse_code": "...",
    "name": "Low Density Residential",
    "notes": null
  },
  "centroid_lon_lat": [-112.49, 40.62]
}
```

## Analysis guidance
When interpreting results:
- **Property codes**: `GZ4` = Greenbelt (agricultural tax deferral). This signals the parcel is actively farmed or has agricultural status — important for subdivision or rezone feasibility.
- **Jurisdiction determination**: Cross-reference the zoning layer hit with municipality boundaries. Erda City incorporated in 2021 and many parcels still have outdated county zoning.
- **General Plan alignment**: A rezone application has much better odds if the requested zone aligns with the General Plan future land use designation.
- **Market value**: `TotalMarket` is the county assessor's market value — useful as a floor for land valuation, not a ceiling.
