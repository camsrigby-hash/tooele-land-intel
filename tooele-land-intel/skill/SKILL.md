---
name: tooele-parcel-analysis
description: |
  Use this skill when the user asks for an analysis of a Tooele County, Utah
  parcel (Grantsville, Erda, Tooele City, or unincorporated county) for
  potential acquisition, entitlement, or development. Triggers on phrases
  like "analyze this parcel", "what should I do with parcel X",
  "highest and best use", "developer outreach", or any Tooele County parcel
  ID matching the pattern NN-NNN-N-NNNN. The skill produces a structured
  development-feasibility memo and a target buyer/developer list, grounded
  in scraped planning agenda data when available.
---

# Tooele Parcel Analysis Skill

## Inputs you should gather before writing the memo

1. **Parcel lookup output** — the markdown report from `parcel-lookup.yml`.
   If the user hasn't provided one, ask them to run the lookup workflow
   (or open a GitHub issue titled `lookup: <parcel-id>`).
2. **Current zoning and General Plan future land use** — these are NOT in
   the LIR feed. Ask the user to provide them, or look them up via the
   reference files below.
3. **Recent agenda items within ~3 miles** — load `data/agenda_items.csv`
   and filter to rows whose `parcels` or `addresses` are geographically
   nearby. If no centroid was extracted, fall back to text proximity
   (same body of road, same subdivision name).
4. **Surrounding land uses** — the user (or you, via web search) should
   identify what's adjacent: residential, agricultural, industrial, etc.

## Memo structure (always produce this)

### 1. Site profile
- Parcel ID, jurisdiction, acreage, owner of record
- Current zoning + summary of allowed uses (cite `references/<juris>-zoning.md`)
- General Plan future land use designation + what it implies
- Notable physical constraints (wetlands, slope, floodplain, utilities)

### 2. Entitlement landscape
- What approvals would be required for the candidate uses?
  (rezone? subdivision? annexation? CUP? GPA?)
- Recent comparable items from the agenda CSV — what got approved or
  denied nearby in the past 12 months. Include date, body, and snippet.
- Which jurisdiction's planning staff is the right first call?

### 3. Highest-and-best-use analysis
Score 3-5 candidate uses against:
- Legally permissible (current zoning + likelihood of rezone)
- Physically possible (acreage, access, utilities)
- Financially feasible (current absorption in market)
- Maximally productive (residual land value)

Recommend a primary use and a backup.

### 4. Buyer / developer target list
For the recommended use, list 8-15 specific entities active in the
Tooele Valley. Group by type:
- **Local/regional homebuilders** (e.g., D.R. Horton, Ivory Homes,
  Fieldstone, Edge Homes, Visionary Homes, Hamlet Homes)
- **Industrial developers** (e.g., NorthPoint, Stack Real Estate,
  Hines, Industrial Realty Group)
- **Mixed-use / commercial** (e.g., CenterCal, Woodbury, Wadsworth)
- **Land bankers / merchant builders** who flip entitled paper
- **Municipal / institutional** (school district, county, UDOT)

For each, give a one-line "why them" rationale based on their recent
Tooele Valley activity (cite the agenda CSV row if applicable).

### 5. Next steps for the seller
Concrete sequence: pre-application meeting → annexation petition (if
needed) → entitlement budget → utility study → marketing package.

## How to handle missing data

- If the agenda CSV is empty (workflow hasn't run yet), say so explicitly
  in the memo and proceed with a zoning-only analysis.
- Never invent a comparable. If you can't find one, write
  "No comparable found in scraped data — recommend manual review of
  recent meeting minutes."
- Always include a "Confidence" line at the top of the memo:
  HIGH (parcel data + zoning + 5+ comps) /
  MEDIUM (parcel data + zoning, sparse comps) /
  LOW (parcel data only).

## Reference files
- `references/grantsville-zoning.md` — summary of Grantsville zone districts
- `references/erda-zoning.md` — summary of Erda zone districts
- `references/tooele-county-zoning.md` — unincorporated zones (MG, A-20, etc.)
- `references/tooele-city-zoning.md` — Tooele City zones
