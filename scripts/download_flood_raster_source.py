"""Download and extract the official Walloon 2 m flood-hazard raster."""
from __future__ import annotations

import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "flood"
RAW.mkdir(parents=True, exist_ok=True)

URL = "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/68cfc9ff-651a-4757-b792-6bde0e238c2f/ALEA_INOND_2020__ALEA_MAP_GEOTIFF_31370.zip"
ZIP_PATH = RAW / "wallonia_flood_hazard_2020_31370.zip"
RASTER_PATH = RAW / "wallonia_flood_hazard_2020_31370.tif"


def main() -> None:
    if RASTER_PATH.exists() and RASTER_PATH.stat().st_size > 1_000_000:
        print(f"Using cached flood raster: {RASTER_PATH.stat().st_size:,} bytes", flush=True)
        return

    temporary_zip = ZIP_PATH.with_suffix(".zip.part")
    with requests.get(
        URL,
        headers={"User-Agent": "STA2RE-territorial-indicators/0.5"},
        stream=True,
        timeout=(30, 240),
    ) as response:
        response.raise_for_status()
        with temporary_zip.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    temporary_zip.replace(ZIP_PATH)

    with zipfile.ZipFile(ZIP_PATH) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"Corrupt archive member: {bad_member}")
        members = [name for name in archive.namelist() if name.lower().endswith((".tif", ".tiff"))]
        if not members:
            raise RuntimeError(f"No GeoTIFF found in {ZIP_PATH.name}: {archive.namelist()[:20]}")
        member = max(members, key=lambda name: archive.getinfo(name).file_size)
        temporary_raster = RASTER_PATH.with_suffix(".tif.part")
        with archive.open(member) as source, temporary_raster.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
        temporary_raster.replace(RASTER_PATH)

    print(
        f"Official flood raster ready: {RASTER_PATH.name} "
        f"({RASTER_PATH.stat().st_size:,} bytes)",
        flush=True,
    )


if __name__ == "__main__":
    main()
