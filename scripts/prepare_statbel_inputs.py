"""Download only the Statbel inputs needed for non-ICU indicators."""
from __future__ import annotations
import zipfile
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
SECTORS_URL = "https://statbel.fgov.be/sites/default/files/files/opendata/Statistische%20sectoren/sh_statbel_statistical_sectors_31370_20260101.geojson.zip"
POPULATION_URL = "https://statbel.fgov.be/sites/default/files/files/opendata/bevolking/sectoren/OPENDATA_SECTOREN_2026.xlsx"
HEADERS = {"User-Agent": "STA2RE-territorial-indicators/0.2 (open-data research)"}

def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 100_000:
        print(f"Using cached {target.name} ({target.stat().st_size:,} bytes)", flush=True)
        return
    response = requests.get(url, headers=HEADERS, timeout=240)
    response.raise_for_status()
    if len(response.content) < 100_000:
        raise RuntimeError(f"Unexpectedly small Statbel download: {url}")
    target.write_bytes(response.content)
    print(f"Downloaded {target.name}: {len(response.content):,} bytes", flush=True)

def main() -> None:
    archive_path = RAW / "statbel_sectors_2026_31370.geojson.zip"
    workbook_path = RAW / "statbel_population_by_sector_2026.xlsx"
    sectors_path = RAW / "sh_statbel_statistical_sectors_31370_20260101.geojson"
    download(SECTORS_URL, archive_path)
    download(POPULATION_URL, workbook_path)
    if not sectors_path.exists() or sectors_path.stat().st_size < 100_000:
        with zipfile.ZipFile(archive_path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith((".geojson", ".json"))]
            if len(members) != 1:
                raise RuntimeError(f"Expected one Statbel GeoJSON, found: {members}")
            sectors_path.write_bytes(archive.read(members[0]))
    print(f"Statbel inputs ready: {sectors_path.name}, {workbook_path.name}", flush=True)

if __name__ == "__main__":
    main()
