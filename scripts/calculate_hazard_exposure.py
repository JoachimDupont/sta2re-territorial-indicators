"""Calculate forest-fire and flood-hazard exposure for the STA2RE study area.

Population estimates use areal weighting within Statbel statistical sectors.
Fire probabilities are intersected as polygons; flood classes are counted from
the official 2 m raster. Outputs are generated at sector, statistical-quarter
and municipality levels.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import requests
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import transform as window_transform
from shapely.geometry import mapping
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REFERENCE = ROOT / "data" / "reference"
PROCESSED = ROOT / "data" / "processed"
INTERIM = ROOT / "data" / "interim"
for folder in (RAW, PROCESSED, INTERIM):
    folder.mkdir(parents=True, exist_ok=True)

SECTORS_FILE = RAW / "sh_statbel_statistical_sectors_31370_20260101.geojson"
POPULATION_FILE = RAW / "statbel_population_by_sector_2026.xlsx"
COMMUNES_FILE = REFERENCE / "communes_zone_etude_icu.geojson"
FIRE_ZIP = RAW / "awac_fire_probability_31370.zip"
FLOOD_ZIP = RAW / "wallonia_flood_hazard_raster_31370.zip"
FIRE_URL = "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/7ae4b87a-d33f-4f5e-b0b2-46f35833c06c/AWAC_ALEA_INCENDIE_GEOPACKAGE_31370.zip"
FLOOD_URL = "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/68cfc9ff-651a-4757-b792-6bde0e238c2f/ALEA_INOND_2020__ALEA_MAP_GEOTIFF_31370.zip"
FIRE_SERVICE = "https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_ALEA_INCENDIE/MapServer/0"
FIRE_CATALOGUE = "https://geoportail.wallonie.be/catalogue/7ae4b87a-d33f-4f5e-b0b2-46f35833c06c.html"
FLOOD_CATALOGUE = "https://geoportail.wallonie.be/catalogue/68cfc9ff-651a-4757-b792-6bde0e238c2f.html"
HEADERS = {"User-Agent": "STA2RE-territorial-indicators/0.2 (open-data research)"}

FLOOD_LEVELS = {
    "very_low": "Aléa très faible",
    "low": "Aléa faible",
    "medium": "Aléa moyen",
    "high": "Aléa élevé",
}
# The official ALEA_INOND data model: 1xx = overflow, 2xx = runoff,
# 3xx = combined overflow + runoff; suffix 01/10/20/30 = level.
FLOOD_CODES = {
    101: ("overflow", "very_low"), 110: ("overflow", "low"),
    120: ("overflow", "medium"), 130: ("overflow", "high"),
    201: ("runoff", "very_low"), 210: ("runoff", "low"),
    220: ("runoff", "medium"), 230: ("runoff", "high"),
    301: ("combined", "very_low"), 310: ("combined", "low"),
    320: ("combined", "medium"), 330: ("combined", "high"),
}
FLOOD_CODES_SIMPLE = {1: "very_low", 10: "low", 20: "medium", 30: "high"}

def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 100_000:
        print(f"Using cached {target.name} ({target.stat().st_size:,} bytes)")
        return
    response = requests.get(url, headers=HEADERS, timeout=300)
    response.raise_for_status()
    if len(response.content) < 100_000:
        raise RuntimeError(f"Unexpectedly small source download: {url} ({len(response.content)} bytes)")
    target.write_bytes(response.content)
    print(f"Downloaded {target.name}: {len(response.content):,} bytes")

def extract_one(archive_path: Path, suffixes: tuple[str, ...], destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(suffixes)]
        if not members:
            raise RuntimeError(f"No {suffixes} found in {archive_path.name}: {archive.namelist()[:20]}")
        # Keep the largest matching source file if the package contains sidecars or previews.
        member = max(members, key=lambda n: archive.getinfo(n).file_size)
        target = destination / Path(member).name
        if not target.exists() or target.stat().st_size != archive.getinfo(member).file_size:
            target.write_bytes(archive.read(member))
        return target

def load_population_sectors():
    sectors = gpd.read_file(SECTORS_FILE).to_crs("EPSG:31370")
    population = pd.read_excel(POPULATION_FILE, sheet_name="Blad1", dtype={"CD_REFNIS": str, "CD_SECTOR": str})
    communes = gpd.read_file(COMMUNES_FILE).to_crs("EPSG:31370")
    selected = set(communes["ADMUKEY"].astype(str))
    sectors = sectors[sectors["cd_munty_refnis"].astype(str).isin(selected)].copy()
    population = population[population["CD_REFNIS"].astype(str).isin(selected)].copy()
    population["TOTAL"] = pd.to_numeric(population["TOTAL"], errors="raise").fillna(0).astype(int)
    if sectors.empty or population.empty:
        raise RuntimeError("Statbel geometry or population data is empty for the 29-commune study area.")
    if sectors["cd_sector"].duplicated().any() or population["CD_SECTOR"].duplicated().any():
        raise RuntimeError("Duplicate statistical-sector identifiers detected.")
    joined = sectors.merge(population[["CD_SECTOR", "TOTAL"]], left_on="cd_sector",
                           right_on="CD_SECTOR", how="left", validate="one_to_one")
    if joined["TOTAL"].isna().any():
        raise RuntimeError(f"Population missing for {int(joined['TOTAL'].isna().sum())} statistical sectors.")
    joined["population_total"] = pd.to_numeric(joined["TOTAL"], errors="raise").astype(int)
    joined = joined.drop(columns=["CD_SECTOR", "TOTAL"])
    return joined, communes, population

def fire_renderer_metadata():
    response = requests.get(FIRE_SERVICE, params={"f": "pjson"}, headers=HEADERS, timeout=90)
    response.raise_for_status()
    meta = response.json()
    if "error" in meta:
        raise RuntimeError(f"Fire MapServer metadata error: {meta['error']}")
    renderer = (meta.get("drawingInfo") or {}).get("renderer") or {}
    unique_infos = renderer.get("uniqueValueInfos") or []
    break_infos = renderer.get("classBreakInfos") or []
    field = renderer.get("field1") or renderer.get("field")
    if unique_infos:
        classes = [{"value": str(x.get("value")), "label": str(x.get("label") or x.get("value"))}
                   for x in unique_infos if x.get("value") is not None]
    elif break_infos:
        lower = float(renderer.get("minValue", 0))
        classes = []
        for i, item in enumerate(break_infos, start=1):
            upper = float(item.get("classMaxValue"))
            classes.append({"value": str(i), "min": lower, "max": upper,
                            "label": str(item.get("label") or f"Classe {i}")})
            lower = upper
    else:
        classes = []
    if not field or len(classes) != 4:
        raise RuntimeError(
            "Cannot safely identify the four fire-probability classes from the official renderer. "
            f"Renderer type={renderer.get('type')}, field={field}, classes={classes}"
        )
    return meta, field, classes

def load_fire_layer():
    download(FIRE_URL, FIRE_ZIP)
    gpkg = extract_one(FIRE_ZIP, (".gpkg",), RAW)
    layers = pyogrio.list_layers(gpkg)
    polygon_layers = [str(row[0]) for row in layers if len(row) > 1 and str(row[1]).lower().find("polygon") >= 0]
    layer_name = polygon_layers[0] if polygon_layers else str(layers[0][0])
    fire = gpd.read_file(gpkg, layer=layer_name).to_crs("EPSG:31370")
    meta, renderer_field, classes = fire_renderer_metadata()
    field_lookup = {c.lower(): c for c in fire.columns}
    if renderer_field.lower() not in field_lookup:
        raise RuntimeError(f"Fire class field '{renderer_field}' from MapServer not found in GeoPackage columns {list(fire.columns)}")
    field = field_lookup[renderer_field.lower()]
    observed = set(fire[field].dropna().astype(str).unique())
    missing = [c["value"] for c in classes if c["value"] not in observed]
    print(f"Fire polygons: {len(fire):,}; class field: {field}; classes: {classes}; not represented in package: {missing}")
    if fire.empty or fire.geometry.isna().all():
        raise RuntimeError("The official fire-probability GeoPackage contains no usable polygons.")
    return fire, field, classes, meta

def fire_metrics(sectors: gpd.GeoDataFrame, fire: gpd.GeoDataFrame, field: str, classes: list[dict]):
    # Build one dissolved geometry per class, so overlapping source features of the
    # same class are not counted twice.
    class_geometries = {}
    for item in classes:
        if "min" in item and "max" in item:
            values = pd.to_numeric(fire[field], errors="coerce")
            mask = (values >= item["min"]) & (values <= item["max"] if item is classes[-1] else values < item["max"])
            subset = fire.loc[mask, "geometry"]
        else:
            subset = fire.loc[fire[field].astype(str) == item["value"], "geometry"]
        class_geometries[item["value"]] = unary_union(list(subset)) if len(subset) else None
    total_union = unary_union([g for g in class_geometries.values() if g is not None])
    rows = []
    for _, sector in sectors.iterrows():
        geom = sector.geometry
        row = {}
        total_geom = geom.intersection(total_union) if total_union is not None else None
        total_area = total_geom.area if total_geom is not None and not total_geom.is_empty else 0.0
        row["fire_area_total_m2"] = float(total_area)
        row["fire_population_est"] = float(sector["population_total"]) * total_area / geom.area if geom.area else 0.0
        for i, item in enumerate(classes, start=1):
            class_geom = class_geometries[item["value"]]
            inter = geom.intersection(class_geom) if class_geom is not None else None
            area = inter.area if inter is not None and not inter.is_empty else 0.0
            row[f"fire_area_class_{i}_m2"] = float(area)
            row[f"fire_population_class_{i}_est"] = float(sector["population_total"]) * area / geom.area if geom.area else 0.0
            row[f"fire_population_class_{i}_pct"] = row[f"fire_population_class_{i}_est"] / sector["population_total"] * 100 if sector["population_total"] else np.nan
            row[f"fire_area_class_{i}_pct"] = area / geom.area * 100 if geom.area else np.nan
        row["fire_population_pct"] = row["fire_population_est"] / sector["population_total"] * 100 if sector["population_total"] else np.nan
        row["fire_area_pct"] = row["fire_area_total_m2"] / geom.area * 100 if geom.area else np.nan
        rows.append(row)
    return pd.DataFrame(rows, index=sectors.index)

def flood_metrics(sectors: gpd.GeoDataFrame, raster_path: Path):
    rows = []
    with rasterio.open(raster_path) as ds:
        if ds.crs is None or ds.crs.to_epsg() != 31370:
            raise RuntimeError(f"Flood raster must use EPSG:31370; got {ds.crs}")
        pixel_area = abs(ds.transform.a * ds.transform.e - ds.transform.b * ds.transform.d)
        if not pixel_area:
            raise RuntimeError("Flood raster has invalid pixel area.")
        observed = set()
        for _, sector in sectors.iterrows():
            geom = sector.geometry
            row = {}
            try:
                window = geometry_window(ds, [mapping(geom)], boundless=False)
                data = ds.read(1, window=window)
                inside = geometry_mask([mapping(geom)], out_shape=data.shape,
                    transform=window_transform(window, ds.transform), invert=True, all_touched=False)
                values = data[inside]
                if ds.nodata is not None:
                    values = values[values != ds.nodata]
            except Exception:
                values = np.array([], dtype="int32")
            vals, counts = np.unique(values, return_counts=True) if len(values) else ([], [])
            count_map = {int(v): int(c) for v, c in zip(vals, counts)}
            observed.update(count_map)
            areas = {level: 0.0 for level in FLOOD_LEVELS}
            type_areas = {kind: {level: 0.0 for level in FLOOD_LEVELS} for kind in ("overflow", "runoff", "combined")}
            for code, count in count_map.items():
                if code in FLOOD_CODES:
                    kind, level = FLOOD_CODES[code]
                    area = count * pixel_area
                    areas[level] += area
                    type_areas[kind][level] += area
                elif code in FLOOD_CODES_SIMPLE:
                    areas[FLOOD_CODES_SIMPLE[code]] += count * pixel_area
                elif code == 0:
                    continue
                else:
                    # Ignore only explicit raster background/no-data codes. Unknown
                    # positive codes must be reviewed rather than silently classified.
                    if code > 0:
                        raise RuntimeError(f"Unrecognised positive flood raster code {code}. Expected official ALEA_INOND CLASSEMENT codes.")
            total_area = sum(areas.values())
            row["flood_area_total_m2"] = float(total_area)
            row["flood_population_est"] = float(sector["population_total"]) * total_area / geom.area if geom.area else 0.0
            row["flood_area_pct"] = total_area / geom.area * 100 if geom.area else np.nan
            row["flood_population_pct"] = row["flood_population_est"] / sector["population_total"] * 100 if sector["population_total"] else np.nan
            row["flood_raster_valid_area_m2"] = float(sum(count_map.values()) * pixel_area)
            row["flood_raster_coverage_pct"] = min(100.0, row["flood_raster_valid_area_m2"] / geom.area * 100) if geom.area else np.nan
            for level, label in FLOOD_LEVELS.items():
                area = areas[level]
                row[f"flood_area_{level}_m2"] = float(area)
                row[f"flood_population_{level}_est"] = float(sector["population_total"]) * area / geom.area if geom.area else 0.0
                row[f"flood_population_{level}_pct"] = row[f"flood_population_{level}_est"] / sector["population_total"] * 100 if sector["population_total"] else np.nan
            for kind, levels in type_areas.items():
                for level, area in levels.items():
                    row[f"flood_{kind}_area_{level}_m2"] = float(area)
                    row[f"flood_{kind}_population_{level}_est"] = float(sector["population_total"]) * area / geom.area if geom.area else 0.0
            rows.append(row)
        print(f"Flood raster: {ds.width}x{ds.height}, resolution={ds.res}, nodata={ds.nodata}, pixel_area={pixel_area}; observed codes={sorted(observed)}")
    return pd.DataFrame(rows, index=sectors.index), {"pixel_area_m2": float(pixel_area), "observed_codes": sorted(int(x) for x in observed)}

def aggregate(sectors: gpd.GeoDataFrame, group_cols: list[str], unit_type: str, communes=None, pop_by_commune=None):
    rows = []
    metric_cols = [c for c in sectors.columns if c.startswith(("fire_area_", "fire_population_", "flood_")) and pd.api.types.is_numeric_dtype(sectors[c])]
    for key, group in sectors.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        geom = group.geometry.union_all()
        row = {
            "unit_type": unit_type, "unit_id": "|".join(str(v) for v in key),
            "municipality_code": str(group.iloc[0]["cd_munty_refnis"]),
            "municipality_name": str(group.iloc[0]["tx_munty_descr_fr"]),
            "population_total": int(group["population_total"].sum()),
            "population_sector_mapped": int(group["population_total"].sum()),
            "population_unmapped": 0,
            "geometry": geom,
        }
        for col in metric_cols:
            row[col] = float(group[col].sum())
        row["fire_population_pct"] = row["fire_population_est"] / row["population_total"] * 100 if row["population_total"] else np.nan
        row["fire_area_pct"] = row["fire_area_total_m2"] / geom.area * 100 if geom.area else np.nan
        row["flood_population_pct"] = row["flood_population_est"] / row["population_total"] * 100 if row["population_total"] else np.nan
        row["flood_area_pct"] = row["flood_area_total_m2"] / geom.area * 100 if geom.area else np.nan
        for level in FLOOD_LEVELS:
            row[f"flood_population_{level}_pct"] = row[f"flood_population_{level}_est"] / row["population_total"] * 100 if row["population_total"] else np.nan
        if unit_type == "quartier_statistique":
            row["statistical_quarter_code"] = str(key[-1])
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=sectors.crs)

def aggregate_communes(sectors, communes, population):
    rows = []
    pop_by_commune = population.groupby("CD_REFNIS")["TOTAL"].sum()
    metric_cols = [c for c in sectors.columns if c.startswith(("fire_area_", "fire_population_", "flood_")) and pd.api.types.is_numeric_dtype(sectors[c])]
    for _, commune in communes.iterrows():
        code = str(commune["ADMUKEY"])
        group = sectors[sectors["cd_munty_refnis"].astype(str) == code]
        if group.empty:
            raise RuntimeError(f"No statistical sectors for commune {code}")
        geom = commune.geometry
        pop_total = int(pop_by_commune.get(code, 0))
        row = {
            "unit_type": "commune", "unit_id": code, "municipality_code": code,
            "municipality_name": str(commune["NAMEFRE"]), "province": str(commune["province"]),
            "population_total": pop_total, "population_sector_mapped": int(group["population_total"].sum()),
            "population_unmapped": pop_total - int(group["population_total"].sum()), "geometry": geom,
        }
        for col in metric_cols:
            row[col] = float(group[col].sum())
        row["fire_population_pct"] = row["fire_population_est"] / pop_total * 100 if pop_total else np.nan
        row["fire_area_pct"] = row["fire_area_total_m2"] / geom.area * 100 if geom.area else np.nan
        row["flood_population_pct"] = row["flood_population_est"] / pop_total * 100 if pop_total else np.nan
        row["flood_area_pct"] = row["flood_area_total_m2"] / geom.area * 100 if geom.area else np.nan
        for level in FLOOD_LEVELS:
            row[f"flood_population_{level}_pct"] = row[f"flood_population_{level}_est"] / pop_total * 100 if pop_total else np.nan
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=communes.crs)

def export(gdf, stem):
    gdf.drop(columns="geometry").to_csv(PROCESSED / f"{stem}.csv", index=False, encoding="utf-8-sig")
    gdf.to_crs("EPSG:4326").to_file(PROCESSED / f"{stem}.geojson", driver="GeoJSON")

def main():
    sectors, communes, population = load_population_sectors()
    print(f"Study sectors: {len(sectors):,}; communes: {len(communes)}")
    fire, fire_field, fire_classes, fire_meta = load_fire_layer()
    fire_stats = fire_metrics(sectors, fire, fire_field, fire_classes)
    flood_raster = extract_one(FLOOD_ZIP, (".tif", ".tiff"), RAW) if FLOOD_ZIP.exists() else None
    if flood_raster is None:
        download(FLOOD_URL, FLOOD_ZIP)
        flood_raster = extract_one(FLOOD_ZIP, (".tif", ".tiff"), RAW)
    flood_stats, flood_raster_report = flood_metrics(sectors, flood_raster)

    sector_output = sectors.copy()
    sector_output = gpd.GeoDataFrame(pd.concat([sector_output, fire_stats, flood_stats], axis=1), geometry="geometry", crs=sectors.crs)
    sector_output["unit_type"] = "secteur_statistique"
    sector_output["unit_id"] = sector_output["cd_sector"].astype(str)
    sector_output["municipality_code"] = sector_output["cd_munty_refnis"].astype(str)
    sector_output["municipality_name"] = sector_output["tx_munty_descr_fr"].astype(str)
    sector_output["sector_name"] = sector_output["tx_sector_descr_fr"].astype(str)

    quarters = aggregate(sector_output, ["cd_munty_refnis", "cd_quarter"], "quartier_statistique")
    quarter_codes = {str(r["unit_id"]): r for _, r in quarters.iterrows()}
    communes_out = aggregate_communes(sector_output, communes, population)

    fire_stem = "fire_exposure"
    flood_stem = "flood_exposure"
    for gdf, stem in ((sector_output, "sectors"), (quarters, "quartiers"), (communes_out, "communes")):
        for i, item in enumerate(fire_classes, start=1):
            gdf[f"fire_class_{i}_label"] = item["label"]
            gdf[f"fire_population_class_{i}_pct"] = gdf[f"fire_population_class_{i}_est"] / gdf["population_total"] * 100
            gdf[f"fire_area_class_{i}_pct"] = gdf[f"fire_area_class_{i}_m2"] / gdf.geometry.area * 100
        export(gdf, f"{fire_stem}_{stem}_2026")
        export(gdf, f"{flood_stem}_{stem}_2026")

    qa = {
        "study_communes": int(len(communes)), "study_sectors": int(len(sectors)), "statistical_quarters": int(len(quarters)),
        "population_total_sectors": int(sectors["population_total"].sum()),
        "fire": {
            "source": FIRE_CATALOGUE, "service_layer": FIRE_SERVICE,
            "production_year": 2024, "catalogue_updated": "24/04/2025",
            "renderer_field": fire_field, "classes": fire_classes,
            "feature_count": int(len(fire)), "crs": fire.crs.to_string(),
            "method": "Union des polygones par classe puis intersection avec les secteurs; surface exposée / surface totale du secteur pour estimer la population (répartition uniforme).",
            "population_estimated_exposed": float(communes_out["fire_population_est"].sum()),
            "area_exposed_km2": float(communes_out["fire_area_total_m2"].sum() / 1_000_000),
        },
        "flood": {
            "source": FLOOD_CATALOGUE, "source_updated": "18/02/2021",
            "raster_resolution_m": [2, 2], "raster": flood_raster.name,
            **flood_raster_report,
            "official_class_codes": {str(k): {"type": v[0], "level": v[1]} for k, v in FLOOD_CODES.items()},
            "level_labels": FLOOD_LEVELS,
            "method": "Comptage des pixels de 2 m dont le centre est dans le secteur; surface de chaque classe / surface géométrique du secteur pour estimer la population (répartition uniforme). Les classes combinées sont comptées une seule fois dans le total.",
            "population_estimated_exposed": float(communes_out["flood_population_est"].sum()),
            "area_exposed_km2": float(communes_out["flood_area_total_m2"].sum() / 1_000_000),
        },
        "population_source": "Statbel, population par secteur statistique au 01/01/2026",
        "population_method_caveat": "Estimations surfaciques, pas une géolocalisation individuelle des habitants.",
    }
    (INTERIM / "hazard_exposure_qa_2026.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    print("Hazard exposure exports completed.")

if __name__ == "__main__":
    main()
