"""Smoke tests: the processed datasets exist and have the expected shape."""
import csv
from nhbot.config import PROCESSED_DIR

def _rows(name):
    with open(PROCESSED_DIR / name) as f:
        return list(csv.DictReader(f))

def test_crosswalk_259_unique_geoids():
    rows = _rows("nh_municipality_geoid_crosswalk.csv")
    assert len(rows) == 259
    geoids = [r["geoid"] for r in rows]
    assert len(set(geoids)) == 259
    ent = [r["entity_type"] for r in rows]
    assert ent.count("city") == 13
    assert ent.count("town") == 221
    assert ent.count("unincorporated") == 25

def test_official_series_years_and_count():
    rows = _rows("nh_equalized_rates_official.csv")
    years = {int(r["vintage"]) for r in rows}
    assert years == {2019, 2020, 2021, 2022, 2023, 2024}
    # every real municipality has a positive rate every year
    bad = [r for r in rows if r["entity_type"] in ("city", "town")
           and (not r["full_value_rate_official"] or float(r["full_value_rate_official"]) <= 0)]
    assert not bad

def test_every_dataset_name_is_in_crosswalk():
    xw = {r["municipality"] for r in _rows("nh_municipality_geoid_crosswalk.csv")}
    off = {r["municipality"] for r in _rows("nh_equalized_rates_official.csv")}
    assert off <= xw
