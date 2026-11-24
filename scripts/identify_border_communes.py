"""Identify Walloon municipalities sharing a land boundary with France.

Run:
    python scripts/identify_border_communes.py

Outputs:
    data/reference/communes_wallonie_frontiere_france.geojson
    data/reference/communes_wallonie_frontiere_france.csv
"""
from pathlib import Path

import geopandas as gpd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reference"
OUT.mkdir(parents=True, exist_ok=True)

WALLONIA_MUNICIPALITIES = (
    "https://geoservices.wallonie.be/arcgis/rest/services/"
    "LIMITES/LIMITES_ADMINISTRATIVES/MapServer/3/query"
)
FRENCH_API = "https://geo.api.gouv.fr/communes"
# Departments identified as bordering Belgium; geometry performs the final test.
FRENCH_BORDER_DEPARTMENTS = ["02", "08", "54", "55", "59"]
METRIC_CRS = "EPSG:31370"
MIN_SHARED_BOUNDARY_M = 0.5


def fetch_walloon_municipalities():
    params = {
        "where": "1=1",
        "outFields": "NAMEFRE,NAMEDUT,ADMUKEY,LOCALID",
        "returnGeometry": "true",
        "outSR": "31370",
        "f": "geojson",
        "resultRecordCount": 2000,
    }
    response = requests.get(WALLONIA_MUNICIPALITIES, params=params, timeout=120)
    response.raise_for_status()
    payload = response.json()
    if "features" not in payload:
        raise RuntimeError(f"ArcGIS response has no features: {payload}")
    gdf = gpd.GeoDataFrame.from_features(payload["features"], crs=METRIC_CRS)
    if gdf.empty:
        raise RuntimeError("No Walloon municipalities returned by ArcGIS.")
    return gdf


def fetch_french_border_municipalities():
    params = [("codeDepartement", code) for code in FRENCH_BORDER_DEPARTMENTS]
    params.extend([
        ("fields", "nom,code,departement"),
        ("format", "geojson"),
        ("geometry", "contour"),
    ])
    response = requests.get(FRENCH_API, params=params, timeout=180)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("features"):
        raise RuntimeError("French communes API returned no geometries.")
    return gpd.GeoDataFrame.from_features(payload["features"], crs="EPSG:4326").to_crs(METRIC_CRS)


def main():
    walloon = fetch_walloon_municipalities()
    french = fetch_french_border_municipalities()
    french_union = french.geometry.union_all()
    french_boundary = french_union.boundary

    rows = []
    for _, municipality in walloon.iterrows():
        geom = municipality.geometry
        if geom is None or geom.is_empty:
            continue
        shared_length = geom.boundary.intersection(french_boundary).length
        if shared_length >= MIN_SHARED_BOUNDARY_M:
            rows.append({
                **municipality.drop(labels="geometry").to_dict(),
                "shared_border_france_m": round(shared_length, 2),
                "geometry": geom,
            })

    if not rows:
        raise RuntimeError(
            "No Walloon municipalities matched. Check French departments, "
            "API response, and coordinate transformations."
        )

    result = gpd.GeoDataFrame(rows, geometry="geometry", crs=METRIC_CRS)
    result = result.sort_values("NAMEFRE")
    geojson_path = OUT / "communes_wallonie_frontiere_france.geojson"
    csv_path = OUT / "communes_wallonie_frontiere_france.csv"
    result.to_crs("EPSG:4326").to_file(geojson_path, driver="GeoJSON")
    result.drop(columns="geometry").to_csv(csv_path, index=False)

    print(f"Walloon municipalities queried: {len(walloon)}")
    print(f"French municipalities queried: {len(french)}")
    print(f"Bordering Walloon municipalities: {len(result)}")
    print(result[["NAMEFRE", "ADMUKEY", "shared_border_france_m"]].to_string(index=False))
    print(f"Wrote: {geojson_path}")
    print(f"Wrote: {csv_path}")


if __name__ == "__main__":
    main()
