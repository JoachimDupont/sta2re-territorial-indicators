"""Download and inspect official Statbel 2026 and AWAC ICU source data."""
from __future__ import annotations
import io, json, zipfile
from pathlib import Path
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RAW, REPORT = ROOT / "data" / "raw", ROOT / "data" / "interim"
RAW.mkdir(parents=True, exist_ok=True); REPORT.mkdir(parents=True, exist_ok=True)
SECTORS_URL = "https://statbel.fgov.be/sites/default/files/files/opendata/Statistische%20sectoren/sh_statbel_statistical_sectors_31370_20260101.geojson.zip"
POPULATION_URL = "https://statbel.fgov.be/sites/default/files/files/opendata/bevolking/sectoren/OPENDATA_SECTOREN_2026.xlsx"
AWAC_RASTER_URL = "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/4073341b-8587-4d71-bf07-b1f07c678b9d/AWAC_ALEAS_CHALEUR_ICU_GEOTIFF_31370.zip"
AWAC_SERVICE = "https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer"
AWAC_LAYER_URL = AWAC_SERVICE + "/12"
AWAC_LEGEND_URL = AWAC_SERVICE + "/legend"
HEADERS = {"User-Agent": "STA2RE-territorial-indicators/0.1 (open-data research)"}

def download(url, target):
    response = requests.get(url, headers=HEADERS, timeout=240); response.raise_for_status()
    if len(response.content) < 100: raise RuntimeError(f"Downloaded file is unexpectedly small: {url}")
    target.write_bytes(response.content); print(f"Downloaded {target.name}: {len(response.content):,} bytes")

def get_json(url):
    response = requests.get(url, params={"f":"pjson"}, headers=HEADERS, timeout=90); response.raise_for_status()
    payload=response.json()
    if payload.get("error"): raise RuntimeError(f"ArcGIS error for {url}: {payload['error']}")
    return payload

def render_sample_for_value(path, value):
    """Cross-check raster code against the official MapServer rendered legend color."""
    with rasterio.open(path) as ds:
        arr=ds.read(1)
        rows, cols=np.where(arr == value)
        if not len(rows): return {"value": int(value), "error":"no sample pixel"}
        row, col=int(rows[len(rows)//2]), int(cols[len(cols)//2])
        x,y=rasterio.transform.xy(ds.transform,row,col,offset="center")
        dx,dy=abs(ds.transform.a),abs(ds.transform.e)
    bbox=f"{x-dx/2},{y-dy/2},{x+dx/2},{y+dy/2}"
    params={"bbox":bbox,"bboxSR":"31370","imageSR":"31370","size":"3,3",
            "format":"png32","transparent":"true","layers":"show:12","f":"image"}
    response=requests.get(AWAC_SERVICE+"/export",params=params,headers=HEADERS,timeout=90)
    response.raise_for_status()
    image=Image.open(io.BytesIO(response.content)).convert("RGBA")
    return {"value":int(value),"sample_x":float(x),"sample_y":float(y),
            "rendered_center_rgba":list(image.getpixel((1,1)))}

def inspect_raster(path):
    with rasterio.open(path) as ds:
        bands=[]
        for band in range(1,ds.count+1):
            values,counts=np.unique(ds.read(band,masked=True).compressed(),return_counts=True)
            try: cmap={str(k):[int(v) for v in vals] for k,vals in ds.colormap(band).items()}
            except Exception: cmap=None
            bands.append({"band":band,"dtype":ds.dtypes[band-1],"description":ds.descriptions[band-1],
              "unique_values":[{"value":float(v),"pixel_count":int(c)} for v,c in zip(values.tolist(),counts.tolist())],
              "color_map":cmap})
        return {"file":path.name,"driver":ds.driver,"width":ds.width,"height":ds.height,
          "band_count":ds.count,"crs":ds.crs.to_string() if ds.crs else None,
          "transform":list(ds.transform)[:6],"bounds":list(ds.bounds),"resolution":list(ds.res),
          "nodata":ds.nodata,"dataset_tags":ds.tags(),"band_reports":bands}

def main():
    sectors_zip=RAW/"statbel_sectors_2026_31370.geojson.zip"
    population_xlsx=RAW/"statbel_population_by_sector_2026.xlsx"
    awac_zip=RAW/"awac_icu_geotiff_31370.zip"
    download(SECTORS_URL,sectors_zip); download(POPULATION_URL,population_xlsx); download(AWAC_RASTER_URL,awac_zip)
    with zipfile.ZipFile(sectors_zip) as archive:
        members=[n for n in archive.namelist() if n.lower().endswith((".geojson",".json"))]
        if len(members)!=1: raise RuntimeError(f"Expected one GeoJSON in Statbel archive, found: {members}")
        extracted=RAW/Path(members[0]).name; extracted.write_bytes(archive.read(members[0]))
    sectors=gpd.read_file(extracted)
    if sectors.empty or sectors.crs is None: raise RuntimeError("Statbel sector geometry is empty or has no declared CRS.")
    if sectors.crs.to_epsg()!=31370: sectors=sectors.to_crs("EPSG:31370")
    with zipfile.ZipFile(awac_zip) as archive:
        tif_members=[n for n in archive.namelist() if n.lower().endswith((".tif",".tiff"))]
        if not tif_members: raise RuntimeError(f"No GeoTIFF found in AWAC archive: {archive.namelist()}")
        awac_tiffs=[]
        for member in tif_members:
            target=RAW/Path(member).name; target.write_bytes(archive.read(member)); awac_tiffs.append(target)
    workbook=pd.ExcelFile(population_xlsx); sheets={}
    for sheet in workbook.sheet_names:
        frame=pd.read_excel(population_xlsx,sheet_name=sheet,nrows=5)
        sheets[sheet]={"columns":[str(c) for c in frame.columns],
                       "sample_rows":frame.fillna("").astype(str).to_dict(orient="records")}
    layer,legend=get_json(AWAC_LAYER_URL),get_json(AWAC_LEGEND_URL)
    presence=next((p for p in awac_tiffs if p.name=="ICU_PRESENCE.tif"),None)
    class_render_checks=[]
    if presence:
        with rasterio.open(presence) as ds:
            vals=np.unique(ds.read(1)).tolist()
        class_render_checks=[render_sample_for_value(presence,v) for v in vals]
    report={"statbel":{"geometry_source_url":SECTORS_URL,"population_source_url":POPULATION_URL,
      "geometry_file":extracted.name,"geometry_feature_count":int(len(sectors)),
      "geometry_crs":sectors.crs.to_string(),"geometry_columns":[str(c) for c in sectors.columns],
      "geometry_sample_properties":sectors.drop(columns="geometry").head(3).fillna("").astype(str).to_dict(orient="records"),
      "population_workbook_sheets":sheets},
      "awac":{"raster_download_url":AWAC_RASTER_URL,"raster_archive":awac_zip.name,
      "rasters":[inspect_raster(p) for p in awac_tiffs],"icu_presence_render_checks":class_render_checks,
      "layer_url":AWAC_LAYER_URL,"layer_metadata":layer,"legend_url":AWAC_LEGEND_URL,"legend":legend}}
    report_path=REPORT/"source_schema_report.json"
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Statbel sectors: {len(sectors):,}; CRS: {sectors.crs}")
    print(f"AWAC GeoTIFFs inspected: {len(awac_tiffs)}; ICU sample render checks: {class_render_checks}")
    print(f"AWAC report: {report_path}")

if __name__=="__main__": main()
