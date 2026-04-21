# Tooele County ArcGIS REST Endpoints

> Verified 2026-04-20 against live county GIS server.
> Base: `https://tcgisws.tooeleco.gov/server/rest/services`

## Parcel Layer
| Item | Value |
|------|-------|
| Service | `Parcels/MapServer` |
| Feature Layer | `/0` |
| Query URL | `https://tcgisws.tooeleco.gov/server/rest/services/Parcels/MapServer/0/query` |
| Parcel ID field | `Parcel_ID` |
| Owner field | `PrimaryOwnerName` |
| All owners | `AllOwners` |
| Tax acreage | `TotalAcres` |
| Geometric acreage | `AcresGeo` |
| Situs address | `SitusAddress` |
| Mailing address | `MailToAddress` |
| Area name | `AreaName` |
| STR | `SectionTownshipRange` |
| Market value | `TotalMarket` |
| Property codes | `PropertyCodes` |
| Subdivision | `Subdivision` |

**Example query (specific parcel):**
```
https://tcgisws.tooeleco.gov/server/rest/services/Parcels/MapServer/0/query
  ?where=Parcel_ID='01-440-0-0019'&outFields=*&f=json
```

## Zoning Layers (`Zoning/MapServer`)
| Layer ID | Name |
|----------|------|
| 1 | Erda City Zoning |
| 2 | Lake Point Zoning |
| 3 | Municipal (Tooele City) Zoning |
| 4 | Tooele County (Unincorporated) Zoning |
| 7 | Grantsville City Zoning |

**Key fields:** `Zone`, `Descript`, `Label`, `Jurisdiction`, `LanduseCode`, `Ordinance`, `Ord_Link`, `ZoningConditions`

**Spatial query example:**
```
https://tcgisws.tooeleco.gov/server/rest/services/Zoning/MapServer/1/query
  ?geometry=-112.49,40.62&geometryType=esriGeometryPoint
  &spatialRel=esriSpatialRelIntersects&inSR=4326
  &outFields=*&f=json
```

## General Plan 2022
| Item | Value |
|------|-------|
| Service | `GeneralPlan_2022_LandUseCA/MapServer` |
| Layer | `/0` |
| Key fields | `Landuse_Ca` (code), `Name`, `Notes` |

## Municipality Boundaries
| Item | Value |
|------|-------|
| Service | `Municipalities/MapServer` |
| Layer 0 | Incorporated municipalities |
| Layer 1 | Unincorporated areas |

## ArcGIS Online (backup / alternative)
- Tooele County Parcels FeatureServer: `https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Tooele/FeatureServer/0`
- LIR parcel data: `https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Tooele_LIR/FeatureServer/0`
