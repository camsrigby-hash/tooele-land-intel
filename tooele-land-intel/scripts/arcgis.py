"""
ArcGIS REST helpers for querying Utah parcel and zoning layers.

The Utah Geospatial Resource Center (UGRC) maintains a statewide LIR
(Land Information Records) parcel layer. We query it by parcel number.

Service IDs change over time. The script discovers the current Tooele
parcel feature service URL from the UGRC ArcGIS Hub item ID, then queries
it. If the discovery fails, we fall back to a known-good URL captured at
last commit. Update FALLBACK_URL when a workflow run reports it stale.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import requests

# UGRC's published item ID for Tooele LIR parcels — stable identifier on ArcGIS Hub.
TOOELE_LIR_ITEM_ID = "7e7839f486d14ff681a8755009d9e2b1"
ARCGIS_ITEM_API = f"https://www.arcgis.com/sharing/rest/content/items/{TOOELE_LIR_ITEM_ID}?f=json"

# Last-known-good. UGRC's standard pattern.
FALLBACK_URL = (
    "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/"
    "UtahLIRParcels_Tooele/FeatureServer/0"
)

# UGRC also publishes a statewide zoning layer. We use this for zoning lookups.
# This is approximate — Grantsville/Erda may have higher-res city layers we
# can chain to. Discover via Hub if it changes.
STATEWIDE_ZONING_URL = (
    "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/"
    "UtahMunicipalBoundaries/FeatureServer/0"
)


def discover_parcel_url() -> str:
    """Try the ArcGIS Hub metadata endpoint to find the current Tooele LIR URL."""
    try:
        r = requests.get(ARCGIS_ITEM_API, timeout=15)
        r.raise_for_status()
        data = r.json()
        url = data.get("url")
        if url:
            return url.rstrip("/") + "/0"
    except Exception:
        pass
    return FALLBACK_URL


def query_parcel(parcel_id: str) -> dict[str, Any] | None:
    """
    Query the Tooele parcel layer for a single parcel ID.
    Returns the parcel's attributes + geometry, or None if not found.
    """
    url = discover_parcel_url()

    # The exact field name for parcel ID varies. Common ones: PARCEL_ID, ParcelID,
    # PARCELNUM, APN. Try them in order.
    for field in ("PARCEL_ID", "ParcelID", "PARCELNUM", "APN", "parcel_id"):
        params = {
            "where": f"{field} = '{parcel_id}'",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",   # WGS84 lat/lon
            "f": "json",
        }
        try:
            r = requests.get(f"{url}/query", params=params, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            features = data.get("features", [])
            if features:
                return features[0]
        except Exception:
            continue

    return None


def query_jurisdiction(lat: float, lon: float) -> dict[str, Any] | None:
    """
    Given a lat/lon, return the municipal boundary feature it falls in
    (city name, county). Used to confirm whether a parcel is inside
    Grantsville, Erda, Tooele City, or unincorporated county.
    """
    params = {
        "geometry": json.dumps({
            "x": lon, "y": lat,
            "spatialReference": {"wkid": 4326},
        }),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        r = requests.get(f"{STATEWIDE_ZONING_URL}/query", params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        features = data.get("features", [])
        if features:
            return features[0]["attributes"]
    except Exception:
        return None
    return None


def parcel_centroid(geometry: dict[str, Any]) -> tuple[float, float] | None:
    """Compute a rough centroid from a polygon parcel geometry (rings, WGS84)."""
    try:
        rings = geometry["rings"]
        pts = [pt for ring in rings for pt in ring]
        if not pts:
            return None
        lon = sum(p[0] for p in pts) / len(pts)
        lat = sum(p[1] for p in pts) / len(pts)
        return lat, lon
    except (KeyError, TypeError, ZeroDivisionError):
        return None
