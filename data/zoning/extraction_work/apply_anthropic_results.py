#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ZONING = ROOT / "data" / "zoning"
WORK = ZONING / "extraction_work"
RESULTS = WORK / "batch_payloads" / "phase18b_batch_results.jsonl"
STATUS = WORK / "batch_payloads" / "phase18b_batch_status.json"
MANIFEST = WORK / "batch_payloads" / "phase18b_batch_manifest.json"
SUMMARY = WORK / "batch_payloads" / "phase18b_extraction_summary.json"

EXTRACTED_AT = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

CITY_NAMES = {
    "grantsville": "Grantsville City",
    "erda": "Erda City",
    "tooele_city": "Tooele City",
    "lehi": "Lehi City",
    "saratoga_springs": "Saratoga Springs City",
    "eagle_mountain": "Eagle Mountain City",
    "south_jordan": "South Jordan City",
    "herriman": "Herriman City",
    "bluffdale": "Bluffdale City",
    "draper": "Draper City",
    "american_fork": "American Fork City",
    "vineyard": "Vineyard City",
    "spanish_fork": "Spanish Fork City",
}

SKIPPED_REASONS = {
    "erda": "Official City Code and Maps page links zoning to an ArcGIS webapp, not a PDF.",
    "tooele_city": "No downloadable official zoning map PDF found; available sources were interactive/code documents rather than base-zoning PDF maps.",
    "saratoga_springs": "Planning and GIS pages advertise interactive city maps; no official base-zoning PDF found.",
    "eagle_mountain": "Planning/engineering sources point to interactive ArcGIS zoning apps/maps; no official base-zoning PDF found.",
    "south_jordan": "FAQ states zoning is available through an interactive zoning map; no official zoning PDF found.",
    "herriman": "GIS/search sources point to interactive zoning maps; no official zoning PDF found.",
    "bluffdale": "Maps/search sources point to ArcGIS zoning web map and map-order page; no official zoning PDF found.",
    "draper": "Planning/development map collection is an ArcGIS Experience zoning map; no official zoning PDF found.",
    "vineyard": "Planning/search sources point to public ArcGIS GIS maps and zoning feature layers; no official base-zoning PDF found.",
}

PDF_PAGES = {
    "american_fork": 1,
    "grantsville": 3,
    "lehi": 1,
    "spanish_fork": 2,
}

# Batch API 50% discount applied to Opus-family base rates. This is a cost estimate derived from
# returned token usage because the Messages Batch result includes usage tokens, not invoice dollars.
BATCH_INPUT_PER_MTOK = 7.50
BATCH_OUTPUT_PER_MTOK = 37.50


def normalize_zone(code: str, desc: str) -> str:
    c = (code or "").upper().strip()
    d = (desc or "").upper()
    if c.startswith("RA") or "RESIDENTIAL / AGRICULT" in d:
        return "Residential-Agriculture"
    if c.startswith("A") or "EXCLUSIVE AGRICULT" in d or d == "AGRICULTURAL":
        return "Agriculture/Rural"
    if c in {"R-R", "RR", "RR-1", "RR-2.5", "RR-5"} or "RURAL" in d:
        return "Residential-Rural"
    if c.startswith("R-1") or c.startswith("R1") or "SINGLE" in d:
        return "Residential-Low"
    if c in {"R-2", "R-3", "R-4", "RM", "R-5"} or "MULTI" in d or "MEDIUM" in d:
        return "Residential-Medium/High"
    if c in {"MU", "M-U"} or "MIXED" in d:
        return "Mixed-Use"
    if c in {"PC", "PUD"} or "PLANNED" in d:
        return "Planned/Master-Planned"
    if c in {"RC", "T-M", "BP"}:
        return "Special/Employment"
    if c.startswith("R") or "RESIDENTIAL" in d:
        return "Residential-General"
    if c in {"NC", "C-1"} or "NEIGHBORHOOD COMMERCIAL" in d:
        return "Commercial-Neighborhood"
    if c.startswith("CC") or "COMMUNITY COMMERCIAL" in d:
        return "Commercial-Community"
    if c.startswith("GC") or c in {"C", "C-2", "UV-C"} or "GENERAL COMMERCIAL" in d or "COMMERCIAL" in d:
        return "Commercial-General"
    if c in {"C-I", "CI"}:
        return "Commercial-Industrial"
    if c in {"LI", "I-1", "M-1"} or "LIGHT INDUSTRIAL" in d:
        return "Industrial-Light"
    if c in {"I-3", "MG-EX", "M-2"} or "HEAVY INDUSTRIAL" in d or "EXTRACTION" in d:
        return "Industrial-Heavy"
    if c in {"I-2", "MG", "MD"} or "MEDIUM INDUSTRIAL" in d or "MANUFACTURING" in d:
        return "Industrial-Medium"
    if "INDUSTRIAL" in d or c.startswith("I"):
        return "Industrial-General"
    if "OPEN" in d or "PARK" in d or c in {"OS", "PF", "PI"}:
        return "Public/Open-Space"
    return "Other/Local"


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON object found in model text")


def valid_ring(coords: Any) -> list[list[float]] | None:
    if not isinstance(coords, list) or len(coords) < 4:
        return None
    ring: list[list[float]] = []
    for pt in coords:
        if not isinstance(pt, list) or len(pt) < 2:
            return None
        try:
            lon = float(pt[0]); lat = float(pt[1])
        except Exception:
            return None
        if not (-114.2 <= lon <= -108.8 and 36.8 <= lat <= 42.2):
            return None
        ring.append([round(lon, 7), round(lat, 7)])
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len({tuple(p) for p in ring}) < 3:
        return None
    return ring


def geometry_polygon(geom: Any) -> dict[str, Any] | None:
    if not isinstance(geom, dict) or geom.get("type") != "Polygon":
        return None
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or not coords:
        return None
    ring = valid_ring(coords[0])
    if not ring:
        return None
    return {"type": "Polygon", "coordinates": [ring]}


def load_results() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parsed = []
    total_input = total_output = 0
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        result = row["result"]
        if result["type"] != "succeeded":
            parsed.append({"custom_id": row["custom_id"], "error": result})
            continue
        msg = result["message"]
        usage = msg.get("usage", {})
        total_input += int(usage.get("input_tokens", 0))
        total_output += int(usage.get("output_tokens", 0))
        text = "".join(c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text")
        data = extract_json(text)
        data["_custom_id"] = row["custom_id"]
        data["_usage"] = usage
        parsed.append(data)
    cost = total_input / 1_000_000 * BATCH_INPUT_PER_MTOK + total_output / 1_000_000 * BATCH_OUTPUT_PER_MTOK
    cost_summary = {"input_tokens": total_input, "output_tokens": total_output, "cost_usd": round(cost, 4), "cost_formula": "Batch input $7.50/MTok + batch output $37.50/MTok"}
    return parsed, cost_summary


def write_geojsons(parsed: list[dict[str, Any]], cost_summary: dict[str, Any]) -> dict[str, Any]:
    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    legends_by_city: dict[str, dict[str, str]] = defaultdict(dict)
    page_notes: dict[str, list[str]] = defaultdict(list)
    source_urls: dict[str, set[str]] = defaultdict(set)
    qualities: dict[str, list[str]] = defaultdict(list)
    rejected = 0

    for data in parsed:
        if "city_slug" not in data:
            continue
        slug = data.get("city_slug")
        source = data.get("source_pdf") or ""
        page_id = data.get("page_id") or data.get("_custom_id")
        source_urls[slug].add(source)
        qualities[slug].append(data.get("extraction_quality", "unknown"))
        page_notes[slug].append(f"{page_id}: {data.get('extraction_quality', 'unknown')} — {data.get('cost_sensitive_summary', '').strip()}")
        for item in data.get("legend", []) or []:
            code = str(item.get("zone_code", "")).strip()
            desc = str(item.get("zone_description", "")).strip()
            if code:
                legends_by_city[slug][code] = desc
        for idx, feat in enumerate(data.get("features", []) or [], 1):
            code = str(feat.get("zone_code", "")).strip()
            desc = str(feat.get("zone_description") or legends_by_city[slug].get(code, "")).strip()
            geom = geometry_polygon(feat.get("geometry"))
            if not code or not geom:
                rejected += 1
                continue
            by_city[slug].append({
                "type": "Feature",
                "properties": {
                    "city_slug": slug,
                    "city_name": CITY_NAMES.get(slug, data.get("city_name", slug)),
                    "zone_code": code,
                    "zone_description": desc,
                    "zone_class_normalized": normalize_zone(code, desc),
                    "source_pdf": source,
                    "source_page_id": str(page_id),
                    "source_feature_index": idx,
                    "extraction_method": "anthropic_batch_vision_claude_opus_4_7",
                    "extraction_quality": data.get("extraction_quality", "unknown"),
                    "confidence": float(feat.get("confidence", 0.0) or 0.0),
                },
                "geometry": geom,
            })

    for slug, name in CITY_NAMES.items():
        features = by_city.get(slug, [])
        if slug in PDF_PAGES:
            status = "extracted_with_anthropic_batch_vision" if features else "vision_completed_no_valid_polygons"
            notes = " | ".join(page_notes.get(slug, []))
        else:
            status = "skipped_no_official_pdf"
            notes = SKIPPED_REASONS.get(slug, "No official base-zoning PDF found.")
        gj = {
            "type": "FeatureCollection",
            "name": f"{slug}_zoning",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": features,
            "metadata": {
                "city_slug": slug,
                "city_name": name,
                "extracted_at": EXTRACTED_AT,
                "extraction_status": status,
                "extraction_model": "claude-opus-4-7",
                "extraction_batch_id": json.loads((WORK / "batch_payloads" / "phase18b_batch_submission.json").read_text()).get("id") if slug in PDF_PAGES else None,
                "notes": notes,
                "source_urls": sorted(source_urls.get(slug, [])),
            },
        }
        (ZONING / f"{slug}.geojson").write_text(json.dumps(gj, indent=2) + "\n", encoding="utf-8")

    stats = {
        "features_by_city": {slug: len(by_city.get(slug, [])) for slug in CITY_NAMES},
        "unique_zones_by_city": {slug: sorted({f["properties"]["zone_code"] for f in by_city.get(slug, [])}) for slug in CITY_NAMES},
        "legend_by_city": {slug: dict(sorted(v.items())) for slug, v in legends_by_city.items()},
        "page_notes": dict(page_notes),
        "source_urls": {k: sorted(v) for k, v in source_urls.items()},
        "qualities": dict(qualities),
        "rejected_features": rejected,
        "cost": cost_summary,
        "processed_pdf_cities": sorted([slug for slug in PDF_PAGES if by_city.get(slug)]),
        "skipped_cities": sorted(SKIPPED_REASONS),
    }
    SUMMARY.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    return stats


def validate_geojsons() -> None:
    for path in sorted(ZONING.glob("*.geojson")):
        data = json.loads(path.read_text())
        assert data["type"] == "FeatureCollection", path
        assert data["crs"]["properties"]["name"] == "EPSG:4326", path
        for feat in data["features"]:
            assert feat["type"] == "Feature", path
            assert feat["geometry"]["type"] == "Polygon", path
            ring = feat["geometry"]["coordinates"][0]
            assert ring[0] == ring[-1], path
            assert feat["properties"].get("zone_code"), path


def main() -> None:
    parsed, cost_summary = load_results()
    stats = write_geojsons(parsed, cost_summary)
    validate_geojsons()
    print(json.dumps({"cost": cost_summary, "features_by_city": stats["features_by_city"], "rejected_features": stats["rejected_features"]}, indent=2))


if __name__ == "__main__":
    main()
