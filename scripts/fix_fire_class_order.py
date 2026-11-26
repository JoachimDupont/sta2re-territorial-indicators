"""Repair the published Tournai fire-class ordering without recomputing geometry.

The first pilot intersected the correct source polygons but paired the ArcGIS
legend labels with the categorical values in alphabetical order. This script
permutes the already-calculated per-class attributes into increasing severity.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
LEVELS = ("sectors", "quartiers", "communes")
ORDER = (2, 4, 3, 1)  # new class index -> old class index
LABELS = ("Faible", "Moyen-faible", "Moyen-élevé", "Élevé")
EXPECTED_OLD_CODES = ("High", "Low", "Medium hi", "Medium low")
EXPECTED_NEW_CODES = ("Low", "Medium low", "Medium hi", "High")
FIELDS = (
    "fire_class_{i}_label",
    "fire_population_class_{i}_est",
    "fire_population_class_{i}_pct",
    "fire_area_class_{i}_m2",
    "fire_area_class_{i}_pct",
)


def read_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main():
    qa_path = DATA / "fire_exposure_tournai_qa_2026.json"
    qa = read_json(qa_path)
    current_codes = tuple(str(item["value"]) for item in qa["classes"])

    if current_codes == EXPECTED_NEW_CODES:
        print("Fire classes already use the official increasing-probability order; nothing to do.")
        return
    if current_codes != EXPECTED_OLD_CODES:
        raise RuntimeError(f"Unexpected existing class order: {current_codes}")

    for level in LEVELS:
        path = DATA / f"fire_exposure_tournai_{level}_2026.geojson"
        geojson = read_json(path)
        for feature in geojson["features"]:
            props = feature["properties"]
            old_values = {
                old_i: {
                    field.format(i=old_i): props.get(field.format(i=old_i))
                    for field in FIELDS
                }
                for old_i in range(1, 5)
            }
            for new_i, old_i in enumerate(ORDER, start=1):
                for field in FIELDS:
                    key = field.format(i=new_i)
                    props[key] = old_values[old_i][field.format(i=old_i)]
                props[f"fire_class_{new_i}_label"] = LABELS[new_i - 1]
        write_json(path, geojson)
        print(f"Corrected {path.name}: {len(geojson['features'])} features")

    by_code = {str(item["value"]): item for item in qa["classes"]}
    qa["classes"] = [
        {"value": code, "label": label}
        for code, label in zip(EXPECTED_NEW_CODES, LABELS)
    ]
    qa["class_order"] = "Probabilité croissante : faible à élevée"
    qa["class_mapping_corrected"] = True
    qa["class_mapping_note"] = (
        "Les statistiques par classe ont été réordonnées à partir des codes "
        "FIRE_PROBA officiels; les géométries et les totaux d'exposition "
        "n'ont pas été recalculés ni modifiés."
    )
    write_json(qa_path, qa)
    print("Corrected QA metadata. Totals and geometries are unchanged.")


if __name__ == "__main__":
    main()
