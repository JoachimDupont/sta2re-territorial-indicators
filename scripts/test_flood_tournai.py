"""Experimental flood-hazard exposure calculation for Tournai only.

Workflow trigger: isolated Tournai pilot, no changes to other published indicators.

Uses the official Walloon 2 m flood-hazard raster. Each pixel is assigned to
the statistical sector containing its centre; population is estimated by
areal weighting within each sector. Outputs are isolated from production data
until the workflow's explicit publication step.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import transform as window_transform
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from calculate_hazard_exposure import (  # noqa: E402
    FLOOD_CODES,
    FLOOD_CODES_SIMPLE,
    FLOOD_LEVELS,
    FLOOD_CATALOGUE,
    load_population_sectors,
)

OUT = ROOT / "data" / "experimental"
OUT.mkdir(parents=True, exist_ok=True)
RASTER_PATH = ROOT / "data" / "raw" / "flood" / "wallonia_flood_hazard_2020_31370.tif"
LEVELS = tuple(FLOOD_LEVELS.keys())
TYPES = ("overflow", "runoff", "combined")


def calculate_sector_metrics(sectors: gpd.GeoDataFrame):
    rows = []
    observed_codes: set[int] = set()
    started = time.perf_counter()

    with rasterio.open(RASTER_PATH) as dataset:
        if dataset.crs is None or dataset.crs.to_epsg() != 31370:
            raise RuntimeError(f"Flood raster must use EPSG:31370; got {dataset.crs}")
        pixel_area = abs(
            dataset.transform.a * dataset.transform.e
            - dataset.transform.b * dataset.transform.d
        )
        if pixel_area <= 0:
            raise RuntimeError(f"Invalid raster pixel area: {pixel_area}")
        resolution = [abs(float(dataset.transform.a)), abs(float(dataset.transform.e))]

        print(
            f"[flood] Raster {dataset.width}x{dataset.height}; "
            f"resolution={resolution}; nodata={dataset.nodata}; "
            f"pixel_area={pixel_area:g} m²",
            flush=True,
        )

        for number, (_, sector) in enumerate(sectors.iterrows(), start=1):
            geometry = sector.geometry
            try:
                window = geometry_window(dataset, [mapping(geometry)], boundless=False)
                pixels = dataset.read(1, window=window)
                inside = geometry_mask(
                    [mapping(geometry)],
                    out_shape=pixels.shape,
                    transform=window_transform(window, dataset.transform),
                    invert=True,
                    all_touched=False,
                )
                values = pixels[inside]
            except Exception as exc:
                raise RuntimeError(
                    f"Could not read flood raster for sector {sector['cd_sector']}: {exc}"
                ) from exc

            if dataset.nodata is not None:
                values = values[values != dataset.nodata]

            unique, counts = np.unique(values, return_counts=True) if values.size else ([], [])
            count_by_code = {int(value): int(count) for value, count in zip(unique, counts)}
            observed_codes.update(count_by_code)

            unknown = sorted(
                code for code in count_by_code
                if code > 0 and code not in FLOOD_CODES and code not in FLOOD_CODES_SIMPLE
            )
            if unknown:
                raise RuntimeError(
                    f"Unrecognised positive flood raster codes in sector "
                    f"{sector['cd_sector']}: {unknown}. Known codes: "
                    f"{sorted(set(FLOOD_CODES) | set(FLOOD_CODES_SIMPLE))}"
                )

            areas = {level: 0.0 for level in LEVELS}
            type_areas = {
                kind: {level: 0.0 for level in LEVELS}
                for kind in TYPES
            }
            valid_pixel_count = int(sum(count_by_code.values()))

            for code, count in count_by_code.items():
                if code == 0:
                    continue
                if code in FLOOD_CODES:
                    kind, level = FLOOD_CODES[code]
                    area = count * pixel_area
                    areas[level] += area
                    type_areas[kind][level] += area
                elif code in FLOOD_CODES_SIMPLE:
                    level = FLOOD_CODES_SIMPLE[code]
                    areas[level] += count * pixel_area

            total_area = float(sum(areas.values()))
            sector_area = float(geometry.area)
            population = float(sector["population_total"])
            row = {
                "flood_area_total_m2": total_area,
                "flood_population_est": population * total_area / sector_area if sector_area else 0.0,
                "flood_area_pct": total_area / sector_area * 100 if sector_area else np.nan,
                "flood_population_pct": (
                    total_area / sector_area * 100 if sector_area and population else np.nan
                ),
                "flood_raster_valid_area_m2": float(valid_pixel_count * pixel_area),
                "flood_raster_coverage_pct": (
                    min(100.0, valid_pixel_count * pixel_area / sector_area * 100)
                    if sector_area else np.nan
                ),
            }
            # Population share follows the same within-sector areal-weighting
            # assumption used for the other STA2RE exposure indicators.
            row["flood_population_pct"] = (
                row["flood_population_est"] / population * 100 if population else np.nan
            )

            for level in LEVELS:
                area = float(areas[level])
                row[f"flood_area_{level}_m2"] = area
                row[f"flood_population_{level}_est"] = (
                    population * area / sector_area if sector_area else 0.0
                )
                row[f"flood_population_{level}_pct"] = (
                    row[f"flood_population_{level}_est"] / population * 100
                    if population else np.nan
                )

            for kind in TYPES:
                for level in LEVELS:
                    area = float(type_areas[kind][level])
                    row[f"flood_{kind}_area_{level}_m2"] = area
                    row[f"flood_{kind}_population_{level}_est"] = (
                        population * area / sector_area if sector_area else 0.0
                    )

            rows.append(row)
            if number % 25 == 0 or number == len(sectors):
                print(
                    f"[flood] sectors {number}/{len(sectors)}; "
                    f"observed_codes={sorted(observed_codes)}; "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )

    report = {
        "raster": RASTER_PATH.name,
        "resolution_m": resolution,
        "pixel_area_m2": float(pixel_area),
        "observed_codes": sorted(observed_codes),
    }
    return pd.DataFrame(rows, index=sectors.index), report


def aggregate_units(frame: gpd.GeoDataFrame, group_columns: list[str], unit_type: str):
    metric_columns = [
        column for column in frame.columns
        if column.startswith("flood_") and pd.api.types.is_numeric_dtype(frame[column])
    ]
    rows = []
    for key, group in frame.groupby(group_columns, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        geometry = group.geometry.union_all()
        population = int(group["population_total"].sum())
        row = {
            "unit_type": unit_type,
            "unit_id": "|".join(str(value) for value in key),
            "municipality_code": str(group.iloc[0]["cd_munty_refnis"]),
            "municipality_name": str(group.iloc[0]["tx_munty_descr_fr"]),
            "population_total": population,
            "population_sector_mapped": population,
            "population_unmapped": 0,
            "geometry": geometry,
        }
        for column in metric_columns:
            row[column] = float(group[column].sum())
        area = float(geometry.area)
        row["flood_population_pct"] = (
            row["flood_population_est"] / population * 100 if population else np.nan
        )
        row["flood_area_pct"] = (
            row["flood_area_total_m2"] / area * 100 if area else np.nan
        )
        row["flood_raster_coverage_pct"] = (
            min(100.0, row["flood_raster_valid_area_m2"] / area * 100)
            if area else np.nan
        )
        for level in LEVELS:
            row[f"flood_population_{level}_pct"] = (
                row[f"flood_population_{level}_est"] / population * 100
                if population else np.nan
            )
        if unit_type == "quartier_statistique":
            row["statistical_quarter_code"] = str(key[-1])
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs)


def aggregate_commune(
    sectors: gpd.GeoDataFrame,
    commune: gpd.GeoDataFrame,
    population_frame: pd.DataFrame,
):
    row_commune = commune.iloc[0]
    code = str(row_commune["ADMUKEY"])
    group = sectors[sectors["cd_munty_refnis"].astype(str).eq(code)]
    if group.empty:
        raise RuntimeError(f"No statistical sectors found for Tournai ({code})")
    population_total = int(
        population_frame.loc[
            population_frame["CD_REFNIS"].astype(str).eq(code), "TOTAL"
        ].sum()
    )
    geometry = row_commune.geometry
    metric_columns = [
        column for column in group.columns
        if column.startswith("flood_") and pd.api.types.is_numeric_dtype(group[column])
    ]
    row = {
        "unit_type": "commune",
        "unit_id": code,
        "municipality_code": code,
        "municipality_name": str(row_commune["NAMEFRE"]),
        "province": str(row_commune["province"]),
        "population_total": population_total,
        "population_sector_mapped": int(group["population_total"].sum()),
        "population_unmapped": population_total - int(group["population_total"].sum()),
        "geometry": geometry,
    }
    for column in metric_columns:
        row[column] = float(group[column].sum())
    area = float(geometry.area)
    row["flood_population_pct"] = (
        row["flood_population_est"] / population_total * 100 if population_total else np.nan
    )
    row["flood_area_pct"] = (
        row["flood_area_total_m2"] / area * 100 if area else np.nan
    )
    row["flood_raster_coverage_pct"] = (
        min(100.0, row["flood_raster_valid_area_m2"] / area * 100)
        if area else np.nan
    )
    for level in LEVELS:
        row[f"flood_population_{level}_pct"] = (
            row[f"flood_population_{level}_est"] / population_total * 100
            if population_total else np.nan
        )
    return gpd.GeoDataFrame([row], geometry="geometry", crs=sectors.crs)


def main() -> None:
    if not RASTER_PATH.exists():
        raise FileNotFoundError(
            f"Flood raster not found: {RASTER_PATH}. Run scripts/download_flood_raster_source.py first."
        )

    sectors, communes, population = load_population_sectors()
    tournai = communes[
        communes["NAMEFRE"].astype(str).str.casefold().eq("tournai")
    ].copy()
    if len(tournai) != 1:
        raise RuntimeError(f"Expected exactly one Tournai commune, found {len(tournai)}")

    code = str(tournai.iloc[0]["ADMUKEY"])
    sectors = sectors[
        sectors["cd_munty_refnis"].astype(str).eq(code)
    ].copy()
    population = population[
        population["CD_REFNIS"].astype(str).eq(code)
    ].copy()
    if sectors.empty:
        raise RuntimeError("No statistical sectors found for Tournai.")

    print(
        f"Tournai REFNIS={code}; sectors={len(sectors)}; "
        f"population={int(sectors['population_total'].sum()):,}",
        flush=True,
    )

    sector_stats, raster_report = calculate_sector_metrics(sectors)
    output = gpd.GeoDataFrame(
        pd.concat([sectors, sector_stats], axis=1),
        geometry="geometry",
        crs=sectors.crs,
    )
    output["unit_type"] = "secteur_statistique"
    output["unit_id"] = output["cd_sector"].astype(str)
    output["municipality_code"] = code
    output["municipality_name"] = "Tournai"
    output["sector_name"] = output["tx_sector_descr_fr"].astype(str)

    quarters = aggregate_units(
        output, ["cd_munty_refnis", "cd_quarter"], "quartier_statistique"
    )
    municipality = aggregate_commune(output, tournai, population)

    labels = {
        "very_low": "Aléa très faible",
        "low": "Aléa faible",
        "medium": "Aléa moyen",
        "high": "Aléa élevé",
    }
    output_sets = {
        "sectors": output,
        "quartiers": quarters,
        "communes": municipality,
    }
    qa = {
        "scope": "Tournai uniquement",
        "municipality_refnis": code,
        "statistical_sector_count": int(len(sectors)),
        "statistical_quarter_count": int(len(quarters)),
        "population_total": int(sectors["population_total"].sum()),
        "source": FLOOD_CATALOGUE,
        "source_raster": raster_report,
        "official_level_labels": labels,
        "official_class_codes": {
            str(value): {"type": kind, "level": level}
            for value, (kind, level) in FLOOD_CODES.items()
        },
        "simple_level_codes": {
            str(value): level for value, level in FLOOD_CODES_SIMPLE.items()
        },
        "method": (
            "Comptage des pixels de 2 m dont le centre se situe dans chaque secteur "
            "statistique. La surface de chaque niveau d'aléa est rapportée à la surface "
            "du secteur pour estimer la population exposée, sous hypothèse de répartition "
            "uniforme de la population dans le secteur. Les codes combinant débordement "
            "et ruissellement sont comptés une seule fois dans le total."
        ),
        "caveat": (
            "Estimation exploratoire de population à l'échelle statistique; ce n'est ni "
            "une prévision de crue ni une localisation individuelle des habitants. "
            "La cartographie officielle est celle en vigueur, mise à jour le 18/02/2021."
        ),
        "output_feature_counts": {},
    }

    for scale, frame in output_sets.items():
        for level, label in labels.items():
            frame[f"flood_level_{level}_label"] = label
        path = OUT / f"flood_exposure_tournai_{scale}_2026.geojson"
        frame.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
        qa["output_feature_counts"][scale] = int(len(frame))

    (OUT / "flood_exposure_tournai_qa_2026.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(qa, ensure_ascii=False, indent=2), flush=True)
    print("Tournai flood exposure outputs ready.", flush=True)


if __name__ == "__main__":
    main()
