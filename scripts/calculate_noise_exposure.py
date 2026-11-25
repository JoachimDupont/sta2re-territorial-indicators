"""Calculate road and rail strategic noise exposure for STA2RE."""
from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from shapely.ops import unary_union

from calculate_hazard_exposure import load_population_sectors, export, INTERIM

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
NOISE_FILES = {
    "road": ROOT / "data" / "raw" / "noise" / "road_noise_2022.gpkg",
    "rail": ROOT / "data" / "raw" / "noise" / "rail_noise_2022.gpkg",
}

# The official 2022 strategic-noise services are in EPSG:3812.
# Road: Lden=layer 0, Lnight=layer 1.
# Rail: Lnight=layer 0, Lden=layer 1.
SOURCES = {
    "road": {
        "title": "Bruit des axes routiers principaux",
        "service": "https://geoservices.wallonie.be/arcgis/rest/services/INDUSTRIES_SERVICES/BRUITMROADMP_2022/MapServer",
        "catalogue": "https://geoportail.wallonie.be/catalogue/c99a0b95-33d1-47ec-b5e8-f5101a50e5c1.html",
        "layers": {"lden": 0, "lnight": 1},
        "updated": "19/05/2025",
        "observation": "2021",
        "scope": "axes routiers principaux supportant plus de 3 millions de véhicules par an",
    },
    "rail": {
        "title": "Bruit des axes ferroviaires principaux",
        "service": "https://geoservices.wallonie.be/arcgis/rest/services/INDUSTRIES_SERVICES/BRUITMRAILMP_2022/MapServer",
        "catalogue": "https://geoportail.wallonie.be/catalogue/ce880380-2b83-467c-88b7-cfea74e4f255.html",
        "layers": {"lden": 1, "lnight": 0},
        "updated": "19/05/2025",
        "observation": "2021",
        "scope": "axes ferroviaires principaux supportant plus de 30 000 passages de trains par an",
    },
}

def read_local_noise_layer(source_key: str, index: str, sectors: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Read the official local GeoPackage, clipped to the study-area extent."""
    path = NOISE_FILES[source_key]
    if not path.exists():
        raise FileNotFoundError(f"Official {source_key} noise GeoPackage not found: {path}")
    layers = pyogrio.list_layers(path)
    candidates = [str(row[0]) for row in layers if row[1] and "polygon" in str(row[1]).lower()]
    matches = [name for name in candidates if index.lower() in name.lower()]
    if len(matches) != 1:
        raise RuntimeError(f"Cannot uniquely identify {index} polygon layer in {path.name}; "
                           f"available layers: {[(str(x[0]), str(x[1])) for x in layers]}")
    layer = matches[0]
    study_bounds = sectors.to_crs("EPSG:3812").total_bounds
    print(f"Reading {source_key} {index} from {path.name}, layer={layer}", flush=True)
    frame = gpd.read_file(path, layer=layer, engine="pyogrio",
                          columns=["CATEGORY_CODE", "CATEGORY_DESCFR"],
                          bbox=tuple(float(x) for x in study_bounds))
    if frame.empty:
        raise RuntimeError(f"No {source_key} {index} polygons intersect the study area.")
    if frame.crs is None:
        frame = frame.set_crs("EPSG:3812")
    frame = frame.to_crs("EPSG:31370")
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    print(f"Loaded {len(frame):,} {source_key} {index} polygons.", flush=True)
    return frame


def class_lower_bound(code: str) -> int:
    match = re.search(r"(?:Lden|Lnight)(\d{2})", str(code), re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"GreaterThan(\d+)", str(code), re.I)
    if match:
        return int(match.group(1))
    raise RuntimeError(f"Unrecognised official noise category code: {code!r}")

def metrics_for_index(sectors: gpd.GeoDataFrame, noise: gpd.GeoDataFrame, prefix: str, index: str):
    if noise.empty:
        raise RuntimeError(f"No official noise polygons returned for {prefix} {index}.")
    required = {"CATEGORY_CODE", "geometry"}
    if not required.issubset(noise.columns):
        raise RuntimeError(f"Noise layer missing fields {required - set(noise.columns)}")
    categories = []
    for code, group in noise.groupby(noise["CATEGORY_CODE"].astype(str), sort=False):
        lower = class_lower_bound(code)
        label_values = group["CATEGORY_DESCFR"].dropna().astype(str).unique().tolist() if "CATEGORY_DESCFR" in group else []
        categories.append({"code": code, "lower": lower, "label": label_values[0] if label_values else code,
                           "geometry": unary_union(list(group.geometry))})
    categories.sort(key=lambda x: (x["lower"], x["code"]))
    all_union = unary_union([item["geometry"] for item in categories])
    rows = []
    for _, sector in sectors.iterrows():
        geom = sector.geometry
        row = {}
        inter_total = geom.intersection(all_union)
        total_area = float(inter_total.area) if not inter_total.is_empty else 0.0
        pop = float(sector["population_total"])
        denom = float(geom.area)
        row[f"{prefix}_{index}_area_total_m2"] = total_area
        row[f"{prefix}_{index}_population_est"] = pop * total_area / denom if denom else 0.0
        row[f"{prefix}_{index}_population_pct"] = row[f"{prefix}_{index}_population_est"] / pop * 100 if pop else np.nan
        row[f"{prefix}_{index}_area_pct"] = total_area / denom * 100 if denom else np.nan
        for i, item in enumerate(categories, start=1):
            inter = geom.intersection(item["geometry"])
            area = float(inter.area) if not inter.is_empty else 0.0
            row[f"{prefix}_{index}_area_class_{i}_m2"] = area
            row[f"{prefix}_{index}_population_class_{i}_est"] = pop * area / denom if denom else 0.0
            row[f"{prefix}_{index}_class_{i}_label"] = item["label"]
            row[f"{prefix}_{index}_class_{i}_lower_db"] = item["lower"]
        # Common threshold indicators: 55 dB Lden and 50 dB Lnight.
        threshold = 55 if index == "lden" else 50
        threshold_union = unary_union([x["geometry"] for x in categories if x["lower"] >= threshold])
        threshold_inter = geom.intersection(threshold_union) if not threshold_union.is_empty else None
        threshold_area = float(threshold_inter.area) if threshold_inter is not None and not threshold_inter.is_empty else 0.0
        row[f"{prefix}_{index}_area_ge_{threshold}_m2"] = threshold_area
        row[f"{prefix}_{index}_population_ge_{threshold}_est"] = pop * threshold_area / denom if denom else 0.0
        row[f"{prefix}_{index}_population_ge_{threshold}_pct"] = row[f"{prefix}_{index}_population_ge_{threshold}_est"] / pop * 100 if pop else np.nan
        rows.append(row)
    return pd.DataFrame(rows, index=sectors.index), categories

def aggregate_units(frame: gpd.GeoDataFrame, group_cols: list[str], unit_type: str):
    metric_cols = [c for c in frame.columns if c.startswith(("road_noise_", "rail_noise_")) and pd.api.types.is_numeric_dtype(frame[c]) and not c.endswith("_lower_db")]
    rows = []
    for key, group in frame.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        geom = group.geometry.union_all()
        row = {
            "unit_type": unit_type,
            "unit_id": "|".join(str(x) for x in key),
            "municipality_code": str(group.iloc[0]["cd_munty_refnis"]),
            "municipality_name": str(group.iloc[0]["tx_munty_descr_fr"]),
            "population_total": int(group["population_total"].sum()),
            "geometry": geom,
        }
        for col in metric_cols:
            row[col] = float(group[col].sum())
        for source in ("road_noise", "rail_noise"):
            for index in ("lden", "lnight"):
                threshold = 55 if index == "lden" else 50
                pop = row.get(f"{source}_{index}_population_est", 0.0)
                area = row.get(f"{source}_{index}_area_total_m2", 0.0)
                row[f"{source}_{index}_population_pct"] = pop / row["population_total"] * 100 if row["population_total"] else np.nan
                row[f"{source}_{index}_area_pct"] = area / geom.area * 100 if geom.area else np.nan
                high = row.get(f"{source}_{index}_population_ge_{threshold}_est", 0.0)
                row[f"{source}_{index}_population_ge_{threshold}_pct"] = high / row["population_total"] * 100 if row["population_total"] else np.nan
        if unit_type == "quartier_statistique":
            row["statistical_quarter_code"] = str(key[-1])
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs)

def aggregate_communes(frame: gpd.GeoDataFrame, communes: gpd.GeoDataFrame, population: pd.DataFrame):
    rows = []
    pop_by_commune = population.groupby("CD_REFNIS")["TOTAL"].sum()
    metric_cols = [c for c in frame.columns if c.startswith(("road_noise_", "rail_noise_")) and pd.api.types.is_numeric_dtype(frame[c]) and not c.endswith("_lower_db")]
    for _, commune in communes.iterrows():
        code = str(commune["ADMUKEY"])
        group = frame[frame["cd_munty_refnis"].astype(str) == code]
        geom = commune.geometry
        total_pop = int(pop_by_commune.get(code, 0))
        row = {"unit_type": "commune", "unit_id": code, "municipality_code": code,
               "municipality_name": str(commune["NAMEFRE"]), "province": str(commune["province"]),
               "population_total": total_pop, "geometry": geom}
        for col in metric_cols:
            row[col] = float(group[col].sum())
        for source in ("road_noise", "rail_noise"):
            for index in ("lden", "lnight"):
                threshold = 55 if index == "lden" else 50
                pop = row.get(f"{source}_{index}_population_est", 0.0)
                area = row.get(f"{source}_{index}_area_total_m2", 0.0)
                row[f"{source}_{index}_population_pct"] = pop / total_pop * 100 if total_pop else np.nan
                row[f"{source}_{index}_area_pct"] = area / geom.area * 100 if geom.area else np.nan
                high = row.get(f"{source}_{index}_population_ge_{threshold}_est", 0.0)
                row[f"{source}_{index}_population_ge_{threshold}_pct"] = high / total_pop * 100 if total_pop else np.nan
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs)

def main():
    sectors, communes, population = load_population_sectors()
    bounds = sectors.total_bounds
    output = sectors.copy()
    category_metadata = {}
    qa = {
        "study_communes": int(len(communes)),
        "study_sectors": int(len(sectors)),
        "population_source": "Statbel, population par secteur statistique au 01/01/2026",
        "population_method_caveat": "Estimations surfaciques, pas une géolocalisation individuelle des habitants.",
        "noise_sources": {},
    }
    for source_key, config in SOURCES.items():
        prefix = f"{source_key}_noise"
        for index, layer_id in config["layers"].items():
            noise = read_local_noise_layer(source_key, index, sectors)
            stats, categories = metrics_for_index(sectors, noise, prefix, index)
            output = gpd.GeoDataFrame(pd.concat([output, stats], axis=1), geometry="geometry", crs=sectors.crs)
            category_metadata[f"{prefix}_{index}"] = categories
            qa["noise_sources"][f"{source_key}_{index}"] = {
                "source": config["catalogue"], "service_layer": f"{config['service']}/{layer_id}",
                "observation_period": config["observation"], "catalogue_updated": config["updated"],
                "scope": config["scope"], "feature_count_in_study_extent": int(len(noise)),
                "categories": [{"code": x["code"], "label": x["label"], "lower_db": x["lower"]} for x in categories],
                "threshold_db": 55 if index == "lden" else 50,
                "method": "Union des polygones par classe, intersection avec chaque secteur statistique, puis estimation de population au prorata de la surface; répartition uniforme supposée dans le secteur.",
            }
    output["unit_type"] = "secteur_statistique"
    output["unit_id"] = output["cd_sector"].astype(str)
    output["municipality_code"] = output["cd_munty_refnis"].astype(str)
    output["municipality_name"] = output["tx_munty_descr_fr"].astype(str)
    output["sector_name"] = output["tx_sector_descr_fr"].astype(str)
    quarters = aggregate_units(output, ["cd_munty_refnis", "cd_quarter"], "quartier_statistique")
    municipalities = aggregate_communes(output, communes, population)
    for gdf, scale in ((output, "sectors"), (quarters, "quartiers"), (municipalities, "communes")):
        for metric_prefix, categories in category_metadata.items():
            for i, item in enumerate(categories, start=1):
                gdf[f"{metric_prefix}_class_{i}_label"] = item["label"]
        export(gdf, f"noise_exposure_{scale}_2026")
    qa["population_estimated_exposed"] = {
        source: {index: float(municipalities[f"{source}_noise_{index}_population_est"].sum())
                 for index in ("lden", "lnight")}
        for source in ("road", "rail")
    }
    (INTERIM / "noise_exposure_qa_2026.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    print("Strategic noise exposure exports completed.")

if __name__ == "__main__":
    main()
