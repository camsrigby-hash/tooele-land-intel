# Manual parcel lookup checklist

Use this when the automated `parcel-lookup.yml` workflow fails or returns
incomplete data — for example, if UGRC's parcel feed is behind, or the
parcel was recently split.

## 1. Statewide parcel viewer (authoritative)
- https://maps.utah.gov/parcels/
- Search the parcel ID exactly as written, including dashes.
- The right-side panel shows: county, owner, acres, address, value.
- Take a screenshot for the record.

## 2. Confirm jurisdiction
The statewide viewer shows the city if the parcel is incorporated. If
it shows only "Tooele County" with no city, the parcel is unincorporated
and the County is the entitlement authority.

## 3. Current zoning
- **Grantsville**: https://grantsvilleut.gov/ → Maps / Zoning Map (PDF)
- **Erda**: https://erdacity.org/ → look for zoning map link, may need
  to email staff if not posted
- **Tooele City**: https://tooelecity.org/ → Community Development → Zoning
- **Unincorporated**: https://www.tooeleco.gov/ → Community Development → GIS

## 4. Future land use (the "master-planned zone")
This lives in the General Plan, not the zoning ordinance.
- Find the jurisdiction's most recent General Plan PDF.
- Look for the chapter titled "Future Land Use" or "Land Use Element."
- The Future Land Use Map (FLUM) shows what the jurisdiction *intends*
  the parcel to be used for long-term, which may differ from current zoning.

## 5. Utility availability
- Culinary water: ask the city water department
- Sewer: Tooele Valley sewer district or city, depending
- Power: Rocky Mountain Power
- Gas: Dominion Energy
A "will-serve" letter from each is required before site plan approval.

## 6. Record what you find
Paste the results into a comment on the GitHub issue for the parcel
(or open one titled `lookup: <parcel-id>`) so future analyses can
reference it.
