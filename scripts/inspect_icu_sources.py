"""Download and inspect the official Statbel 2026 and AWAC source schemas.

This is an ingestion/QA step: it deliberately does not calculate exposure until
the source field keys and AWAC raster class values have been verified.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REPORT = ROOT / "data" / "interim"
RAW.mkdir(parents=True, exist_ok=True)
REPORT.mkdir(parents=True, exist_ok=True)

SECTORS_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/"
    "Statistische%20sectoren/sh_statbel_statistical_sectors_31370_20260101.geojson.zip"
)
POPULATION_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/"
    "bevolking/sectoren/OPENDATA_SECTOREN_2026.xlsx"
)
AWAC_LAYER_URL = (
    "https://geoservices.wallonie.be/arcgis/rest/services/"
    "CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer/12"
)
AWAC_LEGEND_URL = (
    "https://geoservices.wallonie.be/arcgis/rest/services/"
    "CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer/legend"
)
HEADERS = {"User-Agent": "STA2RE-territorial-indicators/0.1 (open-data research)"}


def download(url: str, target: Path) -> None:
    response = requests.get(url, headers=HEADERS, timeout=180)
    response.raise_for_status()
    if len(response.content) < 100:
        raise RuntimeError(f"Downloaded file is unexpectedly small: {url}")
    target.write_bytes(response.content)
    print(f"Downloaded {target.name}: {len(response.content):,} bytes")


def get_json(url: str) -> dict:
    response = requests.get(url, params={"f": "pjson"}, headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"ArcGIS error for {url}: {payload['error']}")
    return payload


def main() -> None:
    sectors_zip = RAW / "statbel_sectors_2026_31370.geojson.zip"
    population_xlsx = RAW / "statbel_population_by_sector_2026.xlsx"
    download(SECTORS_URL, sectors_zip)
    download(POPULATION_URL, population_xlsx)

    with zipfile.ZipFile(sectors_zip) as archive:
        geojson_members = [n for n in archive.namelist() if n.lower().endswith((".geojson", ".json"))]
        if len(geojson_members) != 1:
            raise RuntimeError(f"Expected one GeoJSON in Statbel archive, found: {geojson_members}")
        member = geojson_members[0]
        extracted = RAW / Path(member).name
        extracted.write_bytes(archive.read(member))

    sectors = gpd.read_file(extracted)
    if sectors.empty or sectors.crs is None:
        raise RuntimeError("Statbel sector geometry is empty or has no declared CRS.")
    if sectors.crs.to_epsg() != 31370:
        sectors = sectors.to_crs("EPSG:31370")

    workbook = pd.ExcelFile(population_xlsx)
    sheets = {}
    for sheet in workbook.sheet_names:
        frame = pd.read_excel(population_xlsx, sheet_name=sheet, nrows=5)
        sheets[sheet] = {
            "columns": [str(c) for c in frame.columns],
            "sample_rows": frame.fillna("").astype(str).to_dict(orient="records"),
        }

    layer = get_json(AWAC_LAYER_URL)
    legend = get_json(AWAC_LEGEND_URL)
    report = {
        "statbel": {
            "geometry_source_url": SECTORS_URL,
            "population_source_url": POPULATION_URL,
            "geometry_file": extracted.name,
            "geometry_feature_count": int(len(sectors)),
            "geometry_crs": sectors.crs.to_string(),
            "geometry_columns": [str(c) for c in sectors.columns],
            "geometry_sample_properties": sectors.drop(columns="geometry").head(3).fillna("").astype(str).to_dict(orient="records"),
            "population_workbook_sheets": sheets,
        },
        "awac": {
            "layer_url": AWAC_LAYER_URL,
            "layer_metadata": layer,
            "legend_url": AWAC_LEGEND_URL,
            "legend": legend,
        },
    }
    report_path = REPORT / "source_schema_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Statbel sectors: {len(sectors):,}; CRS: {sectors.crs}")
    print(f"Geometry fields: {', '.join(map(str, sectors.columns))}")
    for sheet, details in sheets.items():
        print(f"Population workbook sheet: {sheet}")
        print(f"  columns: {', '.join(details['columns'])}")
    print(f"AWAC layer type: {layer.get('type', 'not declared')}")
    print(f"AWAC layer name: {layer.get('name', 'not declared')}")
    print(f"AWAC pixel type: {layer.get('pixelType', 'not declared')}")
    print(f"AWAC report: {report_path}")


if __name__ == "__main__":
    main()
