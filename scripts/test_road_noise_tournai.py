"""Experimental road-noise exposure calculation for Tournai only.

Processes both official road indices (Lden and Lnight), retaining every
official CATEGORY_CODE class. Writes only to data/experimental; it never
publishes or overwrites production site data.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from calculate_hazard_exposure import load_population_sectors
from calculate_noise_exposure import SOURCES, read_local_noise_layer, metrics_for_index, aggregate_units, aggregate_communes

OUT = ROOT / "data" / "experimental"
OUT.mkdir(parents=True, exist_ok=True)
ROAD_GPKG = ROOT / "data" / "raw" / "noise" / "road_noise_2022.gpkg"

def main():
    sectors, communes, population = load_population_sectors()
    tournai = communes[communes["NAMEFRE"].astype(str).str.casefold().eq("tournai")]
    if len(tournai) != 1:
        raise RuntimeError(f"Expected exactly one Tournai commune, found {len(tournai)}")
    code = str(tournai.iloc[0]["ADMUKEY"])
    sectors = sectors[sectors["cd_munty_refnis"].astype(str).eq(code)].copy()
    population = population[population["CD_REFNIS"].astype(str).eq(code)].copy()
    if sectors.empty:
        raise RuntimeError("No statistical sectors found for Tournai.")
    print(f"Tournai REFNIS={code}; {len(sectors):,} statistical sectors; "
          f"population={int(sectors['population_total'].sum()):,}", flush=True)

    layers = [str(row[0]) for row in pyogrio.list_layers(ROAD_GPKG)]
    expected = {"lden": "BRUIT_MROAD_2022__LDEN", "lnight": "BRUIT_MROAD_2022__LNIGHT"}
    missing = [name for name in expected.values() if name not in layers]
    if missing:
        raise RuntimeError(f"Missing expected road layers {missing}; available={layers}")

    output = sectors.copy()
    qa = {
        "scope": "Tournai uniquement",
        "municipality_refnis": code,
        "statistical_sector_count": int(len(sectors)),
        "population_total": int(sectors["population_total"].sum()),
        "source": SOURCES["road"]["catalogue"],
        "source_geopackage": "BRUIT_MROAD_2022_GEOPACKAGE_3812.zip",
        "indices": {},
        "method": "Intersection des polygones officiels par CATEGORY_CODE avec chaque secteur statistique; estimation de population au prorata de la surface, sous hypothèse de répartition uniforme dans chaque secteur.",
        "caveat": "Résultats exploratoires publiés pour validation sur le site; ne constituent pas une localisation individuelle des habitants."
    }
    for index in ("lden", "lnight"):
        started = time.perf_counter()
        noise = read_local_noise_layer("road", index, sectors)
        stats, categories = metrics_for_index(sectors, noise, "road_noise", index)
        output = gpd.GeoDataFrame(pd.concat([output, stats], axis=1), geometry="geometry", crs=sectors.crs)
        qa["indices"][index] = {
            "feature_count_in_tournai_extent": int(len(noise)),
            "category_field": "CATEGORY_CODE",
            "categories": [{"code": x["code"], "label": x["label"], "lower_db": x["lower"]} for x in categories],
            "threshold_db": 55 if index == "lden" else 50,
            "estimated_population_exposed": float(stats[f"road_noise_{index}_population_est"].sum()),
            "area_covered_m2": float(stats[f"road_noise_{index}_area_total_m2"].sum()),
            "processing_seconds": round(time.perf_counter() - started, 1)
        }
    output["unit_type"] = "secteur_statistique"
    output["unit_id"] = output["cd_sector"].astype(str)
    output["municipality_code"] = code
    output["municipality_name"] = "Tournai"
    quarters = aggregate_units(output, ["cd_munty_refnis", "cd_quarter"], "quartier_statistique")
    municipalities = aggregate_communes(output, tournai, population)
    for frame in (output, quarters, municipalities):
        for index in ("lden", "lnight"):
            categories = qa["indices"][index]["categories"]
            for number, item in enumerate(categories, start=1):
                frame[f"road_noise_{index}_class_{number}_label"] = item["label"]
    outputs = {
        "sectors": output,
        "quartiers": quarters,
        "communes": municipalities,
    }
    for scale, frame in outputs.items():
        frame.to_crs("EPSG:4326").to_file(
            OUT / f"road_noise_tournai_{scale}_2026.geojson", driver="GeoJSON"
        )
        qa.setdefault("output_feature_counts", {})[scale] = int(len(frame))
    (OUT / "road_noise_tournai_qa_2026.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2), flush=True)
    print(f"Experimental GeoJSON: {OUT / 'road_noise_tournai_sectors_2026.geojson'}", flush=True)

if __name__ == "__main__":
    main()
