"""Calculate AWAC ICU surface and population exposure for the 29 STA2RE communes."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import transform as window_transform
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REFERENCE = ROOT / "data" / "reference"
PROCESSED = ROOT / "data" / "processed"
INTERIM = ROOT / "data" / "interim"
PROCESSED.mkdir(parents=True, exist_ok=True)
INTERIM.mkdir(parents=True, exist_ok=True)

RASTER = RAW / "ICU_PRESENCE.tif"
SECTORS_FILE = RAW / "sh_statbel_statistical_sectors_31370_20260101.geojson"
POPULATION_FILE = RAW / "statbel_population_by_sector_2026.xlsx"
COMMUNES_FILE = REFERENCE / "communes_zone_etude_icu.geojson"

SOURCE_RASTER = "SPW-AWAC / ISSeP — ICU_PRESENCE.tif"
SOURCE_RASTER_UPDATED = "14/11/2024"
SOURCE_POPULATION = "Statbel — population par secteur statistique, situation 01/01/2026"

def calculate_zonal_counts(ds: rasterio.DatasetReader, geom) -> tuple[int, int, int]:
    """Count pixel centres inside polygon: 0=no ICU, 1=very intense, 2=intense."""
    try:
        window = geometry_window(ds, [mapping(geom)], boundless=False)
    except Exception:
        return 0, 0, 0
    data = ds.read(1, window=window)
    inside = geometry_mask(
        [mapping(geom)],
        out_shape=data.shape,
        transform=window_transform(window, ds.transform),
        invert=True,
        all_touched=False,
    )
    values = data[inside]
    return int(np.count_nonzero(values == 0)), int(np.count_nonzero(values == 1)), int(np.count_nonzero(values == 2))

def calculate_sector_metrics(sectors: gpd.GeoDataFrame, population: pd.DataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    if sectors["cd_sector"].duplicated().any():
        raise ValueError("Statbel geometry has duplicate cd_sector keys.")
    if population["CD_SECTOR"].duplicated().any():
        raise ValueError("Statbel population table has duplicate CD_SECTOR keys.")

    joined = sectors.merge(
        population[["CD_SECTOR", "TOTAL"]],
        left_on="cd_sector",
        right_on="CD_SECTOR",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    missing = joined.loc[joined["_merge"] != "both", "cd_sector"].tolist()
    if missing:
        raise ValueError(f"Population missing for {len(missing)} geometry sectors; examples: {missing[:5]}")
    joined["population_total"] = pd.to_numeric(joined["TOTAL"], errors="raise").astype(int)

    raster_rows = []
    with rasterio.open(RASTER) as ds:
        if ds.crs is None or ds.crs.to_epsg() != 31370:
            raise ValueError(f"Expected AWAC raster EPSG:31370, got {ds.crs}")
        if ds.nodata != 0:
            raise ValueError(f"Unexpected ICU_PRESENCE NoData value: {ds.nodata}")
        pixel_area = abs(ds.transform.a * ds.transform.e - ds.transform.b * ds.transform.d)
        raster_resolution = [float(ds.transform.a), float(abs(ds.transform.e))]
        raster_crs = ds.crs.to_string()
        for geom in joined.geometry:
            n_no_icu, n_very, n_intense = calculate_zonal_counts(ds, geom)
            covered_pixels = n_no_icu + n_very + n_intense
            covered_area = covered_pixels * pixel_area
            very_area = n_very * pixel_area
            intense_area = n_intense * pixel_area
            icu_area = very_area + intense_area
            surface_pct = (icu_area / covered_area * 100) if covered_area else np.nan
            raster_rows.append({
                "raster_pixels_no_icu": n_no_icu,
                "raster_pixels_very_intense": n_very,
                "raster_pixels_intense": n_intense,
                "raster_covered_area_m2": covered_area,
                "icu_area_very_intense_m2": very_area,
                "icu_area_intense_m2": intense_area,
                "icu_area_total_m2": icu_area,
                "icu_surface_pct": surface_pct,
                "raster_coverage_pct": min(100.0, covered_area / geom.area * 100) if geom.area else np.nan,
            })
    metrics = pd.DataFrame(raster_rows, index=joined.index)
    joined = gpd.GeoDataFrame(pd.concat([joined, metrics], axis=1), geometry="geometry", crs=sectors.crs)
    joined["icu_population_est"] = joined["population_total"] * joined["icu_surface_pct"] / 100
    joined["icu_population_pct"] = joined["icu_population_est"] / joined["population_total"] * 100
    joined["source_population"] = SOURCE_POPULATION
    joined["source_icu_raster"] = SOURCE_RASTER
    joined["source_icu_updated"] = SOURCE_RASTER_UPDATED

    qa = {
        "sector_count": int(len(joined)),
        "population_joined_sector_count": int(len(joined)),
        "population_joined_total": int(joined["population_total"].sum()),
        "pixel_area_m2": float(pixel_area),
        "raster_resolution_m": raster_resolution,
        "raster_crs": raster_crs,
        "raster_nodata_zero_interpreted_as": "Pas d'ICU (confirmed transparent in official MapServer rendering)",
        "raster_class_values": {
            "0": "Pas d'ICU",
            "1": "ICU très intense",
            "2": "ICU intense",
        },
    }
    joined = joined.drop(columns=["_merge", "CD_SECTOR", "TOTAL"])
    return joined, qa

def aggregate_groups(sectors: gpd.GeoDataFrame, group_cols: list[str], unit_type: str) -> gpd.GeoDataFrame:
    rows = []
    for key, group in sectors.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        geom = group.geometry.union_all()
        pop_total = int(group["population_total"].sum())
        covered_area = float(group["raster_covered_area_m2"].sum())
        very_area = float(group["icu_area_very_intense_m2"].sum())
        intense_area = float(group["icu_area_intense_m2"].sum())
        icu_area = very_area + intense_area
        pop_est = float(group["icu_population_est"].sum())
        row = {
            "unit_type": unit_type,
            "unit_id": "|".join(str(v) for v in key),
            "municipality_code": str(group.iloc[0]["cd_munty_refnis"]),
            "municipality_name": str(group.iloc[0]["tx_munty_descr_fr"]),
            "population_total": pop_total,
            "population_sector_mapped": pop_total,
            "population_unmapped": 0,
            "raster_covered_area_m2": covered_area,
            "icu_area_very_intense_m2": very_area,
            "icu_area_intense_m2": intense_area,
            "icu_area_total_m2": icu_area,
            "icu_surface_pct": icu_area / covered_area * 100 if covered_area else np.nan,
            "icu_population_est": pop_est,
            "icu_population_pct": pop_est / pop_total * 100 if pop_total else np.nan,
            "raster_coverage_pct": min(100.0, covered_area / geom.area * 100) if geom.area else np.nan,
            "source_population": SOURCE_POPULATION,
            "source_icu_raster": SOURCE_RASTER,
            "source_icu_updated": SOURCE_RASTER_UPDATED,
            "geometry": geom,
        }
        if unit_type == "quartier_statistique":
            row["statistical_quarter_code"] = str(key[-1])
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=sectors.crs)

def aggregate_communes(sectors: gpd.GeoDataFrame, communes: gpd.GeoDataFrame, pop_by_commune: pd.Series) -> gpd.GeoDataFrame:
    rows = []
    for _, commune in communes.iterrows():
        code = str(commune["ADMUKEY"])
        group = sectors[sectors["cd_munty_refnis"].astype(str) == code]
        if group.empty:
            raise ValueError(f"No sectors found for selected commune {commune['NAMEFRE']} ({code})")
        geom = commune.geometry
        pop_sector = int(group["population_total"].sum())
        pop_total = int(pop_by_commune.get(code, 0))
        covered_area = float(group["raster_covered_area_m2"].sum())
        very_area = float(group["icu_area_very_intense_m2"].sum())
        intense_area = float(group["icu_area_intense_m2"].sum())
        icu_area = very_area + intense_area
        pop_est = float(group["icu_population_est"].sum())
        rows.append({
            "unit_type": "commune",
            "unit_id": code,
            "municipality_code": code,
            "municipality_name": str(commune["NAMEFRE"]),
            "province": str(commune["province"]),
            "population_total": pop_total,
            "population_sector_mapped": pop_sector,
            "population_unmapped": pop_total - pop_sector,
            "raster_covered_area_m2": covered_area,
            "icu_area_very_intense_m2": very_area,
            "icu_area_intense_m2": intense_area,
            "icu_area_total_m2": icu_area,
            "icu_surface_pct": icu_area / covered_area * 100 if covered_area else np.nan,
            "icu_population_est": pop_est,
            "icu_population_pct": pop_est / pop_total * 100 if pop_total else np.nan,
            "raster_coverage_pct": min(100.0, covered_area / geom.area * 100) if geom.area else np.nan,
            "source_population": SOURCE_POPULATION,
            "source_icu_raster": SOURCE_RASTER,
            "source_icu_updated": SOURCE_RASTER_UPDATED,
            "geometry": geom,
        })
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=communes.crs)

def export_layer(gdf: gpd.GeoDataFrame, stem: str) -> None:
    csv_path = PROCESSED / f"{stem}.csv"
    geojson_path = PROCESSED / f"{stem}.geojson"
    gdf.drop(columns="geometry").to_csv(csv_path, index=False, encoding="utf-8-sig")
    gdf.to_crs("EPSG:4326").to_file(geojson_path, driver="GeoJSON")

def main() -> None:
    sectors = gpd.read_file(SECTORS_FILE).to_crs("EPSG:31370")
    population = pd.read_excel(POPULATION_FILE, sheet_name="Blad1", dtype={"CD_REFNIS": str, "CD_SECTOR": str})
    communes = gpd.read_file(COMMUNES_FILE).to_crs("EPSG:31370")
    selected_codes = set(communes["ADMUKEY"].astype(str))
    sectors = sectors[sectors["cd_munty_refnis"].astype(str).isin(selected_codes)].copy()
    population = population[population["CD_REFNIS"].astype(str).isin(selected_codes)].copy()
    population["TOTAL"] = pd.to_numeric(population["TOTAL"], errors="raise").fillna(0).astype(int)

    sectors_metrics, qa = calculate_sector_metrics(sectors, population)
    quarter_metrics = aggregate_groups(sectors_metrics, ["cd_munty_refnis", "cd_quarter"], "quartier_statistique")
    pop_by_commune = population.groupby("CD_REFNIS")["TOTAL"].sum()
    commune_metrics = aggregate_communes(sectors_metrics, communes, pop_by_commune)

    # Add readable sector identifiers and retain the stable Statbel keys.
    sector_output = sectors_metrics.copy()
    sector_output["unit_type"] = "secteur_statistique"
    sector_output["unit_id"] = sector_output["cd_sector"].astype(str)
    sector_output["municipality_code"] = sector_output["cd_munty_refnis"].astype(str)
    sector_output["municipality_name"] = sector_output["tx_munty_descr_fr"].astype(str)
    sector_output["sector_name"] = sector_output["tx_sector_descr_fr"].astype(str)
    sector_output["statistical_quarter_code"] = sector_output["cd_quarter"].astype(str)

    export_layer(sector_output, "icu_exposure_sectors_2026")
    export_layer(quarter_metrics, "icu_exposure_quartiers_2026")
    export_layer(commune_metrics, "icu_exposure_communes_2026")

    unmapped = population[~population["CD_SECTOR"].isin(sectors_metrics["cd_sector"])]
    qa.update({
        "commune_count": int(len(communes)),
        "statistical_quarter_count": int(len(quarter_metrics)),
        "population_total_all_29_communes": int(population["TOTAL"].sum()),
        "population_without_locatable_sector": int(unmapped["TOTAL"].sum()),
        "unlocatable_population_rows": int(len(unmapped)),
        "population_mapped_to_sectors": int(sectors_metrics["population_total"].sum()),
        "icu_population_est_all_communes": float(commune_metrics["icu_population_est"].sum()),
        "icu_surface_area_km2_all_communes": float(commune_metrics["icu_area_total_m2"].sum() / 1_000_000),
        "method": {
            "raster_codes": "0 = Pas d'ICU (transparent); 1 = ICU très intense; 2 = ICU intense",
            "pixel_inclusion": "pixel centre dans le polygone (all_touched=False)",
            "icu_surface_pct": "surface ICU intense + très intense / surface raster couverte × 100",
            "icu_population_est": "population du secteur × part surfacique ICU (hypothèse de répartition uniforme)",
            "raster_coverage_pct": "surface des pixels du raster dans l'unité / surface géométrique de l'unité × 100",
            "population_denominator_communes": "population Statbel totale, y compris les personnes sans secteur localisable",
            "time_reference": "population Statbel 2026 exposée selon la cartographie ICU AWAC disponible, mise à jour le 14/11/2024",
        },
        "sources": {
            "population": "https://statbel.fgov.be/fr/open-data?category=209",
            "icu_catalogue": "https://geoportail.wallonie.be/catalogue/4073341b-8587-4d71-bf07-b1f07c678b9d.html",
            "icu_raster": "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/4073341b-8587-4d71-bf07-b1f07c678b9d/AWAC_ALEAS_CHALEUR_ICU_GEOTIFF_31370.zip",
        },
    })
    (INTERIM / "icu_exposure_qa_2026.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    print(f"Outputs written to {PROCESSED}")

if __name__ == "__main__":
    main()
