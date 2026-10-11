"""Rules engine tests. Pure Python, no network, no model."""

import json
from collections import Counter
from pathlib import Path

import pytest

from radar.documents import load_path
from radar.engine import (
    EXEMPTION_CLAIMED,
    HIGH,
    LOW,
    MEDIUM,
    MISSING_CAS,
    MISSING_CONCENTRATION,
    NEAR_LIMIT,
    NOT_DECLARED,
    OVER_LIMIT,
    UNTRACEABLE_EXTRACTION,
    UNVERIFIABLE,
    is_traceable,
    screen,
)
from radar.extract import validate_extraction
from radar.rules import load_active_rules, load_active_synonyms
from tests.perfect_extractions import PERFECT

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
ANSWER_KEY = json.loads((SAMPLES / "answer_key.json").read_text())["samples"]

RULES = load_active_rules()
SYNONYMS = load_active_synonyms()


def item(name="Lead", cas="7439-92-1", raw="0.01", value=0.01, unit="%", source=None, exemption=None):
    return {
        "name": name, "cas": cas, "concentration_raw": raw, "concentration_value": value, "unit": unit,
        "exemption_claimed": exemption is not None, "exemption_text": exemption,
        "source_text": source if source is not None else f"{name} {cas or ''} {raw or ''}".strip(),
    }


def run(*items, document=None):
    """Screen items against a document that contains each item's source text, unless one is given."""
    doc = document if document is not None else "\n".join(i["source_text"] for i in items)
    return screen({"supplier": "Testco", "component": "Part", "substances": list(items)}, doc, RULES, SYNONYMS)


def rated(result):
    return [(f.type, f.substance) for f in result.findings if f.type != NOT_DECLARED]


# ---- OVER_LIMIT

def test_over_limit():
    r = run(item(raw="0.35", value=0.35))
    assert rated(r) == [(OVER_LIMIT, "Lead")]
    assert r.rating == HIGH
    f = r.findings[0]
    assert f.value_pct == 0.35 and f.limit_pct == 0.1
    assert f.rule_id.startswith("eu_rohs@") and f.rule_id.endswith("#lead")


def test_exactly_at_limit_is_not_over_but_is_near():
    assert rated(run(item(raw="0.1", value=0.1))) == [(NEAR_LIMIT, "Lead")]


def test_cadmium_uses_its_stricter_limit():
    r = run(item("Cadmium", "7440-43-9", "0.02", 0.02))
    assert rated(r) == [(OVER_LIMIT, "Cadmium")]
    assert r.findings[0].limit_pct == 0.01


def test_ppm_converted_before_comparison():
    assert rated(run(item(raw="450", value=450, unit="ppm"))) == []          # 0.045 %
    assert rated(run(item(raw="1500", value=1500, unit="ppm"))) == [(OVER_LIMIT, "Lead")]
    assert rated(run(item(raw="1500", value=1500, unit="mg/kg"))) == [(OVER_LIMIT, "Lead")]


def test_greater_than_at_limit_is_over():
    assert rated(run(item(raw=">0.1", value=0.1))) == [(OVER_LIMIT, "Lead")]


# ---- NEAR_LIMIT

@pytest.mark.parametrize("value, expected", [(0.079, []), (0.08, [NEAR_LIMIT]), (0.09, [NEAR_LIMIT])])
def test_near_limit_boundary(value, expected):
    assert [t for t, _ in rated(run(item(raw=str(value), value=value)))] == expected


def test_cadmium_near_its_own_limit():
    r = run(item("Cadmium", "7440-43-9", "0.009", 0.009))
    assert rated(r) == [(NEAR_LIMIT, "Cadmium")]
    assert r.rating == LOW and r.watch and not r.needs_review


def test_less_than_bound_under_limit_is_judged_on_the_bound():
    assert rated(run(item(raw="<0.001", value=0.001))) == []
    assert rated(run(item(raw="<0.09", value=0.09))) == [(NEAR_LIMIT, "Lead")]


# ---- MISSING_CAS / MISSING_CONCENTRATION

def test_missing_cas():
    r = run(item(cas=None))
    assert rated(r) == [(MISSING_CAS, "Lead")]
    assert r.rating == MEDIUM and r.needs_review


def test_missing_cas_still_checks_the_value():
    assert Counter(t for t, _ in rated(run(item(cas=None, raw="0.5", value=0.5)))) == Counter([MISSING_CAS, OVER_LIMIT])


def test_missing_concentration():
    r = run(item(raw=None, value=None, unit=None, source="Lead 7439-92-1 (blank)"))
    assert rated(r) == [(MISSING_CONCENTRATION, "Lead")]
    assert r.rating == MEDIUM


# ---- UNVERIFIABLE

@pytest.mark.parametrize("raw", ["trace amounts", "N/A", "<LOD", "not detected"])
def test_vague_values_unverifiable(raw):
    r = run(item(raw=raw, value=None, unit=None))
    assert rated(r) == [(UNVERIFIABLE, "Lead")]
    assert r.rating == MEDIUM


def test_number_without_unit_unverifiable():
    assert rated(run(item(raw="0.05", value=0.05, unit="unknown"))) == [(UNVERIFIABLE, "Lead")]


def test_upper_bound_above_limit_unverifiable():
    assert rated(run(item(raw="<0.5", value=0.5))) == [(UNVERIFIABLE, "Lead")]


def test_lower_bound_below_limit_unverifiable():
    assert rated(run(item(raw=">0.01", value=0.01))) == [(UNVERIFIABLE, "Lead")]


# ---- EXEMPTION_CLAIMED

def test_exemption_over_limit_routes_to_review_not_high():
    r = run(item(raw="3.2", value=3.2, exemption="Annex III 6(c)"))
    assert rated(r) == [(EXEMPTION_CLAIMED, "Lead")]
    assert r.rating == MEDIUM and r.needs_review
    f = r.findings[0]
    assert f.value_pct == 3.2 and f.limit_pct == 0.1
    assert "above the base limit of 0.1%" in f.message


def test_exemption_within_limit_still_routes_to_review():
    r = run(item(raw="0.01", value=0.01, exemption="Annex III 6(c)"))
    assert rated(r) == [(EXEMPTION_CLAIMED, "Lead")] and r.rating == MEDIUM


def test_exemption_with_vague_value_still_routes_to_review():
    assert rated(run(item(raw="trace", value=None, unit=None, exemption="Annex III 7(a)"))) == [(EXEMPTION_CLAIMED, "Lead")]


# ---- UNTRACEABLE_EXTRACTION

DOCUMENT = "Supplier: Testco (fictional)\n| Lead | 7439-92-1 | 0.01 | % w/w |\n| Mercury | 7439-97-6 | 0.001 | % w/w |"


def test_invented_substance_is_untraceable_and_never_passes():
    """The fake extraction invents cadmium, which the document never mentions."""
    real_lead = item(source="| Lead | 7439-92-1 | 0.01 | % w/w |")
    real_mercury = item("Mercury", "7439-97-6", "0.001", 0.001, source="| Mercury | 7439-97-6 | 0.001 | % w/w |")
    invented = item("Cadmium", "7440-43-9", "0.0001", 0.0001, source="| Cadmium | 7440-43-9 | 0.0001 | % w/w |")
    r = run(real_lead, real_mercury, invented, document=DOCUMENT)
    assert rated(r) == [(UNTRACEABLE_EXTRACTION, "Cadmium")]
    assert r.rating == MEDIUM and r.needs_review
    assert r.findings[0].rule_id.endswith("#cadmium")


def test_invented_over_limit_value_is_not_high():
    """An untraceable entry never produces OVER_LIMIT on its own."""
    invented = item("Cadmium", "7440-43-9", "5", 5.0, source="Cadmium: 5% (lab note)")
    r = run(invented, document=DOCUMENT)
    assert OVER_LIMIT not in {f.type for f in r.findings}
    assert rated(r) == [(UNTRACEABLE_EXTRACTION, "Cadmium")] and r.rating == MEDIUM


def test_invented_unrestricted_substance_still_flagged():
    r = run(item("Copper", "7440-50-8", "2", 2.0, source="Copper 2%"), document=DOCUMENT)
    assert rated(r) == [(UNTRACEABLE_EXTRACTION, "Copper")] and r.findings[0].rule is None


def test_untraceable_does_not_hide_real_over_limit_elsewhere():
    real = item(raw="0.5", value=0.5, source="Lead 0.5%")
    invented = item("Cadmium", "7440-43-9", "0.0001", 0.0001, source="made up")
    r = run(real, invented, document="Lead 0.5%")
    assert Counter(t for t, _ in rated(r)) == Counter([OVER_LIMIT, UNTRACEABLE_EXTRACTION])
    assert r.rating == HIGH and r.needs_review


def test_traceability_ignores_case_and_whitespace_only():
    assert is_traceable("lead  7439-92-1\n0.01", "LEAD 7439-92-1 0.01")
    assert not is_traceable("Lead 7439-92-1 0.02", "LEAD 7439-92-1 0.01")
    assert not is_traceable("", "anything") and not is_traceable(None, "anything")


# ---- NOT_DECLARED

def test_not_declared_is_info_and_does_not_change_rating():
    r = run(item())
    nd = r.of_type(NOT_DECLARED)
    assert len(nd) == 9 and all(f.severity == "Info" for f in nd)
    assert "Lead" not in {f.substance for f in nd}
    assert r.rating == LOW and not r.watch


def test_vague_entry_counts_as_addressed():
    r = run(item(raw="N/A", value=None, unit=None))
    assert "Lead" not in {f.substance for f in r.of_type(NOT_DECLARED)}


def test_matching_uses_aliases_and_cas():
    r = run(item("Cr(VI)", None, "0.5", 0.5), item("Some flame retardant", "1163-19-5", "0.001", 0.001))
    assert (OVER_LIMIT, "Hexavalent chromium") in rated(r)
    assert "Polybrominated diphenyl ethers (PBDE)" not in {f.substance for f in r.of_type(NOT_DECLARED)}


def test_unrestricted_substance_ignored():
    r = run(item("Copper", "7440-50-8", "60", 60.0))
    assert rated(r) == [] and len(r.of_type(NOT_DECLARED)) == 10


# ---- rating rules

def test_rating_precedence_and_ordering():
    r = run(item(raw="0.5", value=0.5), item("Mercury", None, "0.001", 0.001),
            item("Cadmium", "7440-43-9", "0.009", 0.009))
    assert r.rating == HIGH and r.needs_review and r.watch
    assert [f.severity for f in r.findings][:3] == ["High", "Medium", "Low"]


def test_empty_extraction_rates_low_with_all_not_declared():
    r = screen({"supplier": None, "component": None, "substances": []}, "text", RULES, SYNONYMS)
    assert r.rating == LOW and len(r.findings) == 10


def test_result_records_rule_versions():
    r = run(item())
    assert set(r.rule_versions) == {"eu_rohs", "synonyms"}


# ---- end to end against the answer key, using perfect extractions

@pytest.mark.parametrize("filename", sorted(ANSWER_KEY))
def test_perfect_extraction_matches_answer_key(filename):
    expected = ANSWER_KEY[filename]
    extraction = PERFECT[filename]
    assert validate_extraction(json.dumps(extraction))[1] == []   # the fixture itself is schema valid
    doc = load_path(SAMPLES / filename)
    r = screen(extraction, doc.text, RULES, SYNONYMS)
    got = Counter((f.type, f.substance) for f in r.findings)
    want = Counter((f["type"], f["substance"]) for f in expected["expected_findings"])
    assert got == want
    assert r.rating == expected["expected_risk"]
    assert r.watch == expected["watch_flag"]
    assert len(extraction["substances"]) == expected["substance_count"]
