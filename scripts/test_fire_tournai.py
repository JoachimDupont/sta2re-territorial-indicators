"""Isolated Tournai pilot for official forest-fire probability exposure."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from calculate_hazard_exposure import (
    INTERIM, PROCESSED, RAW, export, fire_metrics, load_fire_layer,
    load_population_sectors,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "experimental"
OUT.mkdir(parents=True, exist_ok=True)


def aggregate_units(frame: gpd.GeoDataFrame, group_cols: list[str], unit_type: str) -> gpd.GeoDataFrame:
    rows = []
    metric_cols = [
        c for c in frame.columns
        if c.startswith(("fire_area_", "fire_population_"))
        and pd.api.types.is_numeric_dtype(frame[c])
    ]
    for key, group in frame.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        geom = group.geometry.union_all()
        population = int(group["population_total"].sum())
        row = {
            "unit_type": unit_type,
            "unit_id": "|".join(str(v) for v in key),
            "municipality_code": str(group.iloc[0]["cd_munty_refnis"]),
            "municipality_name": str(group.iloc[0]["tx_munty_descr_fr"]),
            "population_total": population,
            "population_sector_mapped": population,
            "population_unmapped": 0,
            "geometry": geom,
        }
        for col in metric_cols:
            row[col] = float(group[col].sum())
        row["fire_population_pct"] = row["fire_population_est"] / population * 100 if population else np.nan
        row["fire_area_pct"] = row["fire_area_total_m2"] / geom.area * 100 if geom.area else np.nan
        if unit_type == "quartier_statistique":
            row["statistical_quarter_code"] = str(key[-1])
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs)


def aggregate_commune(frame: gpd.GeoDataFrame, communes: gpd.GeoDataFrame, population: pd.DataFrame) -> gpd.GeoDataFrame:
    pop_by_commune = population.groupby("CD_REFNIS")["TOTAL"].sum()
    metric_cols = [
        c for c in frame.columns
        if c.startswith(("fire_area_", "fire_population_"))
        and pd.api.types.is_numeric_dtype(frame[c])
    ]
    rows = []
    for _, commune in communes.iterrows():
        code = str(commune["ADMUKEY"])
        group = frame[frame["cd_munty_refnis"].astype(str) == code]
        if group.empty:
            raise RuntimeError(f"No statistical sectors for commune {code}")
        population_total = int(pop_by_commune.get(code, 0))
        row = {
            "unit_type": "commune",
            "unit_id": code,
            "municipality_code": code,
            "municipality_name": str(commune["NAMEFRE"]),
            "province": str(commune["province"]),
            "population_total": population_total,
            "population_sector_mapped": int(group["population_total"].sum()),
            "population_unmapped": population_total - int(group["population_total"].sum()),
            "geometry": commune.geometry,
        }
        for col in metric_cols:
            row[col] = float(group[col].sum())
        row["fire_population_pct"] = row["fire_population_est"] / population_total * 100 if population_total else np.nan
        row["fire_area_pct"] = row["fire_area_total_m2"] / commune.geometry.area * 100 if commune.geometry.area else np.nan
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=communes.crs)


def main() -> None:
    sectors, communes, population = load_population_sectors()
    tournai = communes[communes["ADMUKEY"].astype(str) == "57081"].copy()
    if len(tournai) != 1:
        raise RuntimeError(f"Expected exactly one Tournai commune polygon; found {len(tournai)}")
    sectors = sectors[sectors["cd_munty_refnis"].astype(str) == "57081"].copy()
    if sectors.empty:
        raise RuntimeError("No Tournai statistical sectors found.")
    population = population[population["CD_REFNIS"].astype(str) == "57081"].copy()
    print(f"Tournai pilot: {len(sectors)} sectors; population={int(sectors['population_total'].sum()):,}", flush=True)

    fire, field, classes, metadata = load_fire_layer()
    # FIRE_PROBA is categorical text, so alphabetical ordering is not severity ordering.
    # Explicitly map the four source codes to the official French legend, low -> high.
    official_labels = {
        "Low": "Faible",
        "Medium low": "Moyen-faible",
        "Medium hi": "Moyen-élevé",
        "High": "Élevé",
    }
    observed_codes = set(fire[field].dropna().astype(str).unique())
    if observed_codes != set(official_labels):
        raise RuntimeError(
            f"Unexpected FIRE_PROBA codes: {sorted(observed_codes)}; "
            f"expected {sorted(official_labels)}"
        )
    classes = [{"value": code, "label": label} for code, label in official_labels.items()]
    print(f"Validated official fire class order (low to high): {classes}", flush=True)
    fire_stats = fire_metrics(sectors, fire, field, classes)
    sector_output = gpd.GeoDataFrame(
        pd.concat([sectors, fire_stats], axis=1), geometry="geometry", crs=sectors.crs
    )
    sector_output["unit_type"] = "secteur_statistique"
    sector_output["unit_id"] = sector_output["cd_sector"].astype(str)
    sector_output["municipality_code"] = "57081"
    sector_output["municipality_name"] = "Tournai"
    sector_output["sector_name"] = sector_output["tx_sector_descr_fr"].astype(str)

    quarters = aggregate_units(
        sector_output, ["cd_munty_refnis", "cd_quarter"], "quartier_statistique"
    )
    communes_out = aggregate_commune(sector_output, tournai, population)

    for gdf in (sector_output, quarters, communes_out):
        for i, item in enumerate(classes, start=1):
            gdf[f"fire_class_{i}_label"] = item["label"]
            gdf[f"fire_population_class_{i}_pct"] = (
                gdf[f"fire_population_class_{i}_est"] / gdf["population_total"] * 100
            )
            gdf[f"fire_area_class_{i}_pct"] = (
                gdf[f"fire_area_class_{i}_m2"] / gdf.geometry.area * 100
            )

    outputs = {
        "sectors": sector_output,
        "quartiers": quarters,
        "communes": communes_out,
    }
    for level, gdf in outputs.items():
        path = OUT / f"fire_exposure_tournai_{level}_2026.geojson"
        gdf.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
        print(f"Wrote {path} ({len(gdf)} features)", flush=True)

    qa = {
        "scope": "Tournai uniquement",
        "municipality_refnis": "57081",
        "study_sector_count": int(len(sector_output)),
        "statistical_quarter_count": int(len(quarters)),
        "commune_count": int(len(communes_out)),
        "population_total": int(sector_output["population_total"].sum()),
        "source": "https://geoportail.wallonie.be/catalogue/7ae4b87a-d33f-4f5e-b0b2-46f35833c06c.html",
        "source_service": "https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_ALEA_INCENDIE/MapServer",
        "source_last_updated": "24/04/2025",
        "source_production_year": 2024,
        "source_feature_count": int(len(fire)),
        "source_crs": fire.crs.to_string(),
        "class_field": field,
        "classes": classes,
        "estimated_exposed_population": float(communes_out["fire_population_est"].sum()),
        "exposed_area_km2": float(communes_out["fire_area_total_m2"].sum() / 1_000_000),
        "method": "Union des polygones de probabilité par classe puis intersection avec les secteurs statistiques; population estimée au prorata de la surface exposée dans chaque secteur, sous hypothèse de répartition uniforme.",
        "population_source": "Statbel, population par secteur statistique au 01/01/2026",
        "caveat": "Estimation exploratoire surfacique, pas une localisation individuelle des habitants ni une prévision de départ de feu. La couche cartographie la probabilité d'incendie des zones boisées, pas l'exposition directe des bâtiments.",
        "output_feature_counts": {k: int(len(v)) for k, v in outputs.items()},
    }
    (OUT / "fire_exposure_tournai_qa_2026.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(qa, ensure_ascii=False, indent=2), flush=True)
    print("Tournai forest-fire pilot completed.", flush=True)


if __name__ == "__main__":
    main()
