"""Select the user-defined 29 Walloon municipalities for the STA²RE ICU pilot.

Run:
    python scripts/identify_border_communes.py

Outputs:
    data/reference/communes_zone_etude_icu.geojson
    data/reference/communes_zone_etude_icu.csv
"""
from pathlib import Path
import unicodedata

import geopandas as gpd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reference"
OUT.mkdir(parents=True, exist_ok=True)

WALLONIA_MUNICIPALITIES = (
    "https://geoservices.wallonie.be/arcgis/rest/services/"
    "LIMITES/LIMITES_ADMINISTRATIVES/MapServer/3/query"
)
METRIC_CRS = "EPSG:31370"

# Explicit study perimeter supplied by the project owner.
TARGET_MUNICIPALITIES = {
    "Hainaut": [
        "Comines-Warneton", "Mouscron", "Estaimpuis", "Tournai", "Brunehaut",
        "Rumes", "Péruwelz", "Hensies", "Quiévrain", "Honnelles", "Dour",
        "Quévy", "Erquelinnes", "Momignies", "Chimay",
    ],
    "Namur": [
        "Couvin", "Viroinval", "Doische", "Hastière", "Beauraing", "Gedinne",
        "Vresse-sur-Semois",
    ],
    "Luxembourg": [
        "Aubange", "Arlon", "Musson", "Virton", "Rouvroy", "Florenville",
        "Bouillon",
    ],
}


def normalize_name(value):
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().replace("’", "'").split())


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


def main():
    walloon = fetch_walloon_municipalities()
    targets = {
        normalize_name(name): province
        for province, names in TARGET_MUNICIPALITIES.items()
        for name in names
    }
    walloon["_normalized_name"] = walloon["NAMEFRE"].map(normalize_name)
    selected = walloon[walloon["_normalized_name"].isin(targets)].copy()

    found = set(selected["_normalized_name"])
    missing = sorted(
        name for name in targets if name not in found
    )
    if missing:
        raise RuntimeError(
            "Target municipalities not found in Walloon reference: "
            + ", ".join(missing)
        )
    if len(selected) != len(targets):
        raise RuntimeError(
            f"Expected {len(targets)} unique municipalities, found {len(selected)}."
        )

    selected["province"] = selected["_normalized_name"].map(targets)
    selected["selection_method"] = "liste_de_reference_fournie_pour_STA2RE"
    selected = selected.drop(columns=["_normalized_name"]).sort_values(
        ["province", "NAMEFRE"]
    )

    geojson_path = OUT / "communes_zone_etude_icu.geojson"
    csv_path = OUT / "communes_zone_etude_icu.csv"
    selected.to_crs("EPSG:4326").to_file(geojson_path, driver="GeoJSON")
    selected.drop(columns="geometry").to_csv(csv_path, index=False)

    print(f"Walloon municipalities queried: {len(walloon)}")
    print(f"Target municipalities expected: {len(targets)}")
    print(f"Target municipalities selected: {len(selected)}")
    print(selected[["province", "NAMEFRE", "ADMUKEY"]].to_string(index=False))
    print(f"Wrote: {geojson_path}")
    print(f"Wrote: {csv_path}")


if __name__ == "__main__":
    main()
