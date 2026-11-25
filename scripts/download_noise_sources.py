"""Download the official Walloon strategic-noise GeoPackages once for local processing."""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

import pyogrio
import requests

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "raw" / "noise"
SOURCES = {
    "road": {
        "url": "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/c99a0b95-33d1-47ec-b5e8-f5101a50e5c1/BRUIT_MROAD_2022_GEOPACKAGE_3812.zip",
        "file": "road_noise_2022.gpkg",
        "expected_zip_mb": 875.7,
    },
    "rail": {
        "url": "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/ce880380-2b83-467c-88b7-cfea74e4f255/BRUIT_MRAIL_2022_GEOPACKAGE_3812.zip",
        "file": "rail_noise_2022.gpkg",
        "expected_zip_mb": 133.1,
    },
}
HEADERS = {"User-Agent": "STA2RE-territorial-indicators/0.3 (open-data research)"}


def download(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 1_000_000:
        print(f"Reuse existing archive: {destination.name} ({destination.stat().st_size:,} bytes)", flush=True)
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 6):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=(30, 180)) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", 0))
                written = 0
                with partial.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
                            written += len(chunk)
                            if written % (50 * 1024 * 1024) < len(chunk):
                                print(f"{destination.name}: {written / 1024**2:.0f} MiB"
                                      + (f" / {total / 1024**2:.0f} MiB" if total else ""), flush=True)
                if total and written != total:
                    raise RuntimeError(f"Incomplete download: received {written}, expected {total} bytes")
            partial.replace(destination)
            print(f"Downloaded {destination.name}: {written:,} bytes", flush=True)
            return
        except Exception as exc:
            partial.unlink(missing_ok=True)
            if attempt == 5:
                raise
            delay = min(15 * attempt, 60)
            print(f"Download attempt {attempt}/5 failed: {exc}; retry in {delay}s", flush=True)
            time.sleep(delay)


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    metadata = {
        "license": "CC-BY 4.0",
        "coordinate_reference_system": "EPSG:3812",
        "sources": {},
    }
    for key, source in SOURCES.items():
        gpkg = TARGET / source["file"]
        if not gpkg.exists() or gpkg.stat().st_size < 1_000_000:
            archive = TARGET / f"{key}_noise_2022.zip"
            download(source["url"], archive)
            with zipfile.ZipFile(archive) as zipped:
                bad = zipped.testzip()
                if bad:
                    raise RuntimeError(f"Corrupt member in {archive.name}: {bad}")
                candidates = [name for name in zipped.namelist() if name.lower().endswith(".gpkg")]
                if len(candidates) != 1:
                    raise RuntimeError(f"Expected one GeoPackage in {archive.name}; found {candidates}")
                member = candidates[0]
                print(f"Extracting {member} to {gpkg}", flush=True)
                temporary = gpkg.with_suffix(".gpkg.part")
                with zipped.open(member) as src, temporary.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                temporary.replace(gpkg)
            archive.unlink(missing_ok=True)
        layers = pyogrio.list_layers(gpkg)
        layer_info = [{"name": str(row[0]), "geometry_type": str(row[1])} for row in layers]
        if not layer_info:
            raise RuntimeError(f"No layers found in {gpkg}")
        metadata["sources"][key] = {
            "url": source["url"],
            "file": source["file"],
            "size_bytes": gpkg.stat().st_size,
            "layers": layer_info,
        }
        print(f"{key.upper()} GeoPackage: {gpkg.stat().st_size:,} bytes", flush=True)
        for layer in layer_info:
            print(f"  layer: {layer['name']} | geometry: {layer['geometry_type']}", flush=True)
    (TARGET / "noise_sources_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Official road and rail noise GeoPackages downloaded and inspected.", flush=True)


if __name__ == "__main__":
    main()
