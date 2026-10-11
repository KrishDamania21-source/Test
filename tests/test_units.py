"""Unit conversion and concentration status, all in Python. No network."""

import pytest

from radar.units import MISSING, NUMERIC, UNKNOWN_UNIT, VAGUE, normalize, numbers_in, qualifier_of


@pytest.mark.parametrize("raw, value, unit, percent", [
    ("0.35", 0.35, "%", 0.35),
    ("450", 450, "ppm", 0.045),
    ("100", 100, "mg/kg", 0.01),
    ("1000 ppm", 1000, "ppm", 0.1),
    ("<0.001", 0.001, "%", 0.001),
])
def test_numeric_conversion_to_percent(raw, value, unit, percent):
    c = normalize(raw, value, unit)
    assert c.status == NUMERIC
    assert c.percent == pytest.approx(percent)


def test_ppm_property_round_trips():
    assert normalize("90", 90, "ppm").ppm == pytest.approx(90)


@pytest.mark.parametrize("raw", ["trace amounts", "N/A", "<LOD", "not detected"])
def test_text_without_number_is_vague(raw):
    assert normalize(raw, None, None).status == VAGUE


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_nothing_given_is_missing(raw):
    assert normalize(raw, None, None).status == MISSING


def test_number_without_unit_is_unknown_unit():
    assert normalize("0.5", 0.5, "unknown").status == UNKNOWN_UNIT
    assert normalize("0.5", 0.5, None).status == UNKNOWN_UNIT


@pytest.mark.parametrize("raw, q", [("<0.001", "<"), ("≤ 5", "<="), ("<= 5", "<="), ("> 2", ">"),
                                    ("~0.1", "~"), ("0.1", "="), (None, "=")])
def test_qualifiers(raw, q):
    assert qualifier_of(raw) == q


def test_numbers_in_text():
    assert numbers_in("<0.001 % w/w") == [0.001]
    assert numbers_in("0,35") == [0.35]
    assert numbers_in("trace amounts") == []
