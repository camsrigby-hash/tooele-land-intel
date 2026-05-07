#!/usr/bin/env python3
"""Score parcels by proximity and AM-peak approach side to major Wasatch Front on-ramps.

Phase 13b-7 implements a proxy score for the "on-ramp funnel" concept until a
WFRC AM-peak skim matrix is available. The script reads local parcel CSV exports,
fetches/caches UGRC Utah Roads on-ramp features for I-15, I-80, US-89, and SR-201,
and writes one scored row per parcel input record.

Output schema:
parcel_id, commute_corridor_score, nearest_ramp_id, distance_mi,
approach_side_bool, commute_corridor_method
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

UGRC_ROADS_QUERY_URL = (
    "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/"
    "UtahRoads/FeatureServer/0/query"
)
ROUTE_PREFIX_TO_CORRIDOR = {
    "0015": "I-15",
    "0080": "I-80",
    "0089": "US-89",
    "0201": "SR-201",
}
ROUTE_PREFIXES = tuple(ROUTE_PREFIX_TO_CORRIDOR)
EARTH_RADIUS_MI = 3958.7613


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def norm_text(value: object) -> str:
    return str(value or "").strip().upper()


def route_prefix(dot_rtname: str) -> Optional[str]:
    text = norm_text(dot_rtname)
    for prefix in ROUTE_PREFIXES:
        if text.startswith(prefix):
            return prefix
    return None


def direction_from_name(name: str, dot_rtname: str) -> Optional[str]:
    text = f" {norm_text(name)} {norm_text(dot_rtname)} "
    for token in ("NB", "SB", "EB", "WB"):
        if f" {token} " in text or token in norm_text(dot_rtname)[4:6]:
            return token
    # UGRC DOT route names commonly use P/N after the 4-digit route. Use this
    # as a fallback only when the text direction is absent.
    rt = norm_text(dot_rtname)
    if len(rt) >= 5:
        sign = rt[4]
        prefix = route_prefix(rt)
        if prefix in ("0015", "0089"):
            return "NB" if sign == "P" else "SB" if sign == "N" else None
        if prefix in ("0080", "0201"):
            return "EB" if sign == "P" else "WB" if sign == "N" else None
    return None


def flatten_line_coords(geometry: Dict) -> List[Tuple[float, float]]:
    coords = geometry.get("coordinates") or []
    if not coords:
        return []
    if geometry.get("type") == "MultiLineString":
        flat: List[Tuple[float, float]] = []
        for part in coords:
            flat.extend((float(x), float(y)) for x, y in part)
        return flat
    return [(float(x), float(y)) for x, y in coords]


def query_ugrc(where: str, offset: int, page_size: int) -> Dict:
    params = {
        "f": "geojson",
        "where": where,
        "outFields": (
            "OBJECTID,FULLNAME,NAME,POSTTYPE,DOT_RTNAME,DOT_HWYNAM,DOT_FCLASS,"
            "DOT_CLASS,ONEWAY,CARTOCODE,UNIQUE_ID,COUNTY_L,COUNTY_R"
        ),
        "returnGeometry": "true",
        "outSR": "4326",
        "resultOffset": offset,
        "resultRecordCount": page_size,
        "orderByFields": "OBJECTID",
    }
    response = requests.get(UGRC_ROADS_QUERY_URL, params=params, timeout=90)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"UGRC query error: {payload['error']}")
    return payload


def fetch_ugrc_onramps(cache_path: Path, refresh: bool = False) -> Dict:
    if cache_path.exists() and not refresh:
        with cache_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    where = (
        "POSTTYPE = 'RAMP' AND "
        "(DOT_RTNAME LIKE '0015%' OR DOT_RTNAME LIKE '0080%' OR "
        "DOT_RTNAME LIKE '0089%' OR DOT_RTNAME LIKE '0201%')"
    )
    page_size = 2000
    offset = 0
    raw_features: List[Dict] = []
    while True:
        payload = query_ugrc(where, offset, page_size)
        page = payload.get("features", [])
        raw_features.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
        time.sleep(0.2)

    onramps: List[Dict] = []
    seen = set()
    for feature in raw_features:
        props = feature.get("properties") or {}
        fullname = props.get("FULLNAME") or props.get("NAME") or ""
        text = norm_text(fullname)
        prefix = route_prefix(props.get("DOT_RTNAME") or "")
        if not prefix:
            continue
        direction = direction_from_name(fullname, props.get("DOT_RTNAME") or "")
        if direction not in {"NB", "SB", "EB", "WB"}:
            continue
        coords = flatten_line_coords(feature.get("geometry") or {})
        if not coords:
            continue
        # The first vertex is treated as the local-street approach point. UGRC
        # ramp features include ON/OFF/connector naming variants; using the full
        # major-route ramp set avoids under-covering interchanges where the
        # one-way ramp is named from the freeway perspective rather than the
        # local-street approach perspective.
        lng, lat = coords[0]
        object_id = props.get("OBJECTID")
        unique_id = props.get("UNIQUE_ID") or f"UGRC_ROAD_{object_id}"
        ramp_id = f"{prefix}_{direction}_{object_id}"
        if ramp_id in seen:
            continue
        seen.add(ramp_id)
        enriched = dict(props)
        ramp_kind = "on" if " ON " in f" {text} " else "off" if " OFF " in f" {text} " else "connector"
        enriched.update(
            {
                "ramp_id": ramp_id,
                "source_objectid": object_id,
                "source_unique_id": unique_id,
                "corridor": ROUTE_PREFIX_TO_CORRIDOR[prefix],
                "route_prefix": prefix,
                "direction": direction,
                "ramp_kind": ramp_kind,
                "method_note": "proxy ramp-funnel point from UGRC Utah Roads major-route ramp first vertex",
            }
        )
        onramps.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": enriched,
            }
        )

    collection = {
        "type": "FeatureCollection",
        "name": "ugrc_onramps_major_commute_corridors_proxy",
        "source": "UGRC Utah Roads FeatureServer/0; POSTTYPE='RAMP'; route prefixes 0015/0080/0089/0201; first vertex used as proxy ramp-funnel point",
        "generated_at_unix": int(time.time()),
        "features": onramps,
    }
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(collection, f, separators=(",", ":"))
    return collection


def load_ramp_arrays(onramps_geojson: Dict) -> Dict[str, np.ndarray]:
    features = onramps_geojson.get("features", [])
    if not features:
        raise RuntimeError("No on-ramp features available for scoring")
    ramp_ids, lngs, lats, dirs = [], [], [], []
    for f in features:
        props = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        ramp_ids.append(str(props.get("ramp_id") or props.get("source_objectid") or ""))
        lngs.append(float(coords[0]))
        lats.append(float(coords[1]))
        dirs.append(str(props.get("direction") or ""))
    return {
        "ids": np.array(ramp_ids, dtype=object),
        "lng": np.radians(np.array(lngs, dtype="float64")),
        "lat": np.radians(np.array(lats, dtype="float64")),
        "lng_deg": np.array(lngs, dtype="float64"),
        "lat_deg": np.array(lats, dtype="float64"),
        "dirs": np.array(dirs, dtype=object),
    }


def score_chunk(df: pd.DataFrame, ramps: Dict[str, np.ndarray]) -> pd.DataFrame:
    required = {"parcel_id", "centroid_lng", "centroid_lat"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Parcel CSV missing required columns: {sorted(missing)}")

    out = pd.DataFrame({"parcel_id": df["parcel_id"].astype(str)})
    lng_deg = pd.to_numeric(df["centroid_lng"], errors="coerce").to_numpy(dtype="float64")
    lat_deg = pd.to_numeric(df["centroid_lat"], errors="coerce").to_numpy(dtype="float64")
    valid = np.isfinite(lng_deg) & np.isfinite(lat_deg)

    n = len(df)
    scores = np.zeros(n, dtype="float64")
    nearest_ids = np.full(n, "", dtype=object)
    nearest_dist = np.full(n, np.nan, dtype="float64")
    approach = np.zeros(n, dtype=bool)

    if valid.any():
        lat1 = np.radians(lat_deg[valid])[:, None]
        lng1 = np.radians(lng_deg[valid])[:, None]
        dlat = ramps["lat"][None, :] - lat1
        dlng = ramps["lng"][None, :] - lng1
        a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(ramps["lat"])[None, :] * np.sin(dlng / 2.0) ** 2
        dist = 2.0 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
        idx = np.argmin(dist, axis=1)
        dmin = dist[np.arange(dist.shape[0]), idx]
        dirs = ramps["dirs"][idx]
        rlat = ramps["lat_deg"][idx]
        rlng = ramps["lng_deg"][idx]
        plat = lat_deg[valid]
        plng = lng_deg[valid]
        app = (
            ((dirs == "NB") & (plat <= rlat))
            | ((dirs == "SB") & (plat >= rlat))
            | ((dirs == "EB") & (plng <= rlng))
            | ((dirs == "WB") & (plng >= rlng))
        )
        sc = np.zeros_like(dmin)
        sc[(dmin <= 2.0)] = 0.1
        sc[(dmin <= 0.5) & (~app)] = 0.4
        sc[(dmin <= 1.0) & app] = 0.4
        sc[(dmin <= 0.5) & app] = 0.7
        sc[(dmin <= 0.25) & app] = 1.0
        valid_positions = np.where(valid)[0]
        scores[valid_positions] = sc
        nearest_ids[valid_positions] = ramps["ids"][idx]
        nearest_dist[valid_positions] = dmin
        approach[valid_positions] = app

    out["commute_corridor_score"] = np.round(scores, 3)
    out["nearest_ramp_id"] = nearest_ids
    out["distance_mi"] = ["" if not np.isfinite(x) else f"{x:.4f}" for x in nearest_dist]
    out["approach_side_bool"] = np.where(approach, "true", "false")
    out["commute_corridor_method"] = "proxy"
    return out


def parcel_files(data_raw_dir: Path) -> List[Path]:
    files = sorted(data_raw_dir.glob("parcels_*.csv")) + sorted(data_raw_dir.glob("parcels_*.csv.gz"))
    # Prefer .csv over .csv.gz if both exist for the same county.
    by_stem: Dict[str, Path] = {}
    for path in files:
        stem = path.name.replace(".csv.gz", "").replace(".csv", "")
        if stem not in by_stem or by_stem[stem].suffix == ".gz":
            by_stem[stem] = path
    return [by_stem[k] for k in sorted(by_stem)]


def write_scores(
    data_raw_dir: Path,
    output_csv: Path,
    ramps: Dict[str, np.ndarray],
    chunk_size: int,
    dedupe_by_parcel_id: bool = True,
) -> Dict[str, float]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    files = parcel_files(data_raw_dir)
    if not files:
        raise FileNotFoundError(f"No parcel CSV files found in {data_raw_dir}")

    tmp_csv = output_csv.with_suffix(output_csv.suffix + ".tmp") if dedupe_by_parcel_id else output_csv
    first_write = True
    raw_rows = 0
    for path in files:
        print(f"Scoring {path}", file=sys.stderr)
        with open_text(path) as f:
            for df in pd.read_csv(f, chunksize=chunk_size, dtype={"parcel_id": "string"}, low_memory=False):
                scored = score_chunk(df, ramps)
                raw_rows += len(scored)
                scored.to_csv(tmp_csv, mode="w" if first_write else "a", header=first_write, index=False)
                first_write = False

    if dedupe_by_parcel_id:
        print("De-duplicating parcel_id rows with last-write-wins semantics to match D1 INSERT OR REPLACE loading", file=sys.stderr)
        df = pd.read_csv(tmp_csv, dtype={"parcel_id": "string"})
        df = df.drop_duplicates("parcel_id", keep="last")
        df.to_csv(output_csv, index=False)
        tmp_csv.unlink(missing_ok=True)
    else:
        df = pd.read_csv(output_csv, dtype={"parcel_id": "string"})

    total = len(df)
    positive = int((df["commute_corridor_score"].astype(float) > 0.0).sum())
    high = int((df["commute_corridor_score"].astype(float) >= 0.7).sum())
    proxy = int((df["commute_corridor_method"] == "proxy").sum())
    return {
        "raw_input_rows": raw_rows,
        "rows": total,
        "positive_rows": positive,
        "positive_pct": positive / total if total else 0.0,
        "score_gte_0_7_rows": high,
        "score_gte_0_7_pct": high / total if total else 0.0,
        "proxy_rows": proxy,
        "proxy_pct": proxy / total if total else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Proxy-score parcel commute corridor/on-ramp funnel value.")
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--data-raw-dir", type=Path, default=repo_root / "data/raw")
    parser.add_argument("--ramp-cache", type=Path, default=repo_root / "data/cache/ugrc_onramps.geojson")
    parser.add_argument("--output", type=Path, default=repo_root / "data/raw/parcel_commute_corridor_scores.csv")
    parser.add_argument("--chunk-size", type=int, default=50000)
    parser.add_argument("--refresh-ramps", action="store_true")
    parser.add_argument("--keep-input-duplicates", action="store_true", help="Write every input row instead of de-duplicating parcel_id rows for D1 parity.")
    args = parser.parse_args()

    onramps = fetch_ugrc_onramps(args.ramp_cache, refresh=args.refresh_ramps)
    ramps = load_ramp_arrays(onramps)
    print(f"Loaded {len(ramps['ids'])} UGRC on-ramp proxy points", file=sys.stderr)
    stats = write_scores(args.data_raw_dir, args.output, ramps, args.chunk_size, dedupe_by_parcel_id=not args.keep_input_duplicates)
    print(json.dumps(stats, indent=2), file=sys.stderr)
    if stats["proxy_pct"] != 1.0:
        raise RuntimeError("Not every row was tagged commute_corridor_method='proxy'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
