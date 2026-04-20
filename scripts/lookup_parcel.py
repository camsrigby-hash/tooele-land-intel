"""
Parcel Lookup — entry point.

Usage:
    python scripts/lookup_parcel.py 01-440-0-0019

Output: prints a Markdown report to stdout. The GitHub Action captures
this and posts it as a comment on the triggering issue.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from utils.arcgis import (
    parcel_centroid,
    query_jurisdiction,
    query_parcel,
)


def format_report(parcel_id: str) -> str:
    feat = query_parcel(parcel_id)
    if feat is None:
        return (
            f"# Parcel `{parcel_id}` — not found\n\n"
            "No record returned by the UGRC Tooele LIR layer. "
            "Possible causes: typo in parcel ID, parcel was retired/split, "
            "or the LIR feed is behind. Verify on https://maps.utah.gov/parcels/ ."
        )

    attrs = feat["attributes"]
    geom = feat.get("geometry", {})
    centroid = parcel_centroid(geom)

    jurisdiction = None
    if centroid:
        jurisdiction = query_jurisdiction(centroid[0], centroid[1])

    # Pull common LIR fields. Names vary by year of refresh; show whatever is present.
    def field(*candidates: str, default: str = "—") -> str:
        for c in candidates:
            if c in attrs and attrs[c] not in (None, "", " "):
                return str(attrs[c])
        return default

    lines = [
        f"# Parcel `{parcel_id}`",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Identity",
        f"- **Owner:** {field('OWN_NAME', 'OwnerName', 'OWNER')}",
        f"- **Address:** {field('PROP_ADD', 'SiteAddress', 'PARCEL_ADD')}",
        f"- **Acres:** {field('TOTAL_MKT_VALUE', 'PARCEL_ACRES', 'Acreage', 'ACRES_GIS')}",
        "",
        "## Jurisdiction",
        f"- **County:** Tooele",
        f"- **Municipality:** {jurisdiction.get('NAME', 'Unincorporated') if jurisdiction else 'Could not determine'}",
        "",
        "## Centroid",
        f"- **Lat/Lon:** {centroid[0]:.6f}, {centroid[1]:.6f}" if centroid else "- _Geometry unavailable_",
        "",
        "## Valuation (as reported in LIR)",
        f"- **Total market value:** {field('TOTAL_MKT_VALUE')}",
        f"- **Land value:** {field('LAND_MKT_VALUE')}",
        "",
        "## Notes for analyst",
        "- Current zoning is **not** in the LIR feed. Confirm via the city/county zoning map.",
        "- Future land use designation is in the General Plan, not the parcel layer.",
        "- See `docs/parcel_lookup_manual.md` for the manual verification checklist.",
        "",
        "<details><summary>Raw attributes</summary>",
        "",
        "```json",
        json.dumps(attrs, indent=2, default=str),
        "```",
        "",
        "</details>",
    ]
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: lookup_parcel.py <parcel_id>", file=sys.stderr)
        return 1
    parcel_id = sys.argv[1].strip()
    print(format_report(parcel_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
