"""Download only the official 2022 rail-noise GeoPackage."""
from __future__ import annotations
import zipfile
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "raw" / "noise"
TARGET.mkdir(parents=True, exist_ok=True)
URL = "https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/ce880380-2b83-467c-88b7-cfea74e4f255/BRUIT_MRAIL_2022_GEOPACKAGE_3812.zip"
GPKG = TARGET / "rail_noise_2022.gpkg"
ZIP = TARGET / "rail_noise_2022.zip"

def main():
    if GPKG.exists() and GPKG.stat().st_size > 1_000_000:
        print(f"Using cached rail GeoPackage ({GPKG.stat().st_size:,} bytes)", flush=True)
        return
    with requests.get(URL, headers={"User-Agent":"STA2RE-territorial-indicators/0.4"}, stream=True, timeout=(30,240)) as response:
        response.raise_for_status()
        with ZIP.open("wb") as out:
            for chunk in response.iter_content(1024*1024):
                if chunk:
                    out.write(chunk)
    with zipfile.ZipFile(ZIP) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt archive member: {bad}")
        members = [n for n in archive.namelist() if n.lower().endswith(".gpkg")]
        if len(members) != 1:
            raise RuntimeError(f"Expected one GeoPackage, found {members}")
        temporary = GPKG.with_suffix(".gpkg.part")
        with archive.open(members[0]) as src, temporary.open("wb") as dst:
            while chunk := src.read(1024*1024):
                dst.write(chunk)
        temporary.replace(GPKG)
    ZIP.unlink(missing_ok=True)
    print(f"Rail GeoPackage ready: {GPKG.stat().st_size:,} bytes", flush=True)

if __name__ == "__main__":
    main()
