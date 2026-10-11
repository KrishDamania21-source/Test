"""Rule pack loader and matcher tests. No network."""

import json
import shutil

import pytest

from radar.rules import (
    COLUMNS,
    RULES_DIR,
    RulePackError,
    SubstanceMatcher,
    cas_checksum_ok,
    load_active_packs,
    load_active_rules,
    load_active_synonyms,
    parse_synonyms_text,
    parse_pack_text,
)

GOOD_ROW = {
    "pack_id": "test_pack",
    "regulation_name": "Test Regulation",
    "short_code": "TEST",
    "jurisdiction": "EU",
    "obligation_type": "restriction",
    "substance": "Lead",
    "cas_numbers": "7439-92-1",
    "limit_pct": "0.1",
    "legal_reference": "Test Annex I",
    "source_url": "https://example.org/test",
    "version_date": "2020-01-01",
    "last_verified": "2026-01-01",
    "verified_by": "Tester",
    "is_subset": "false",
}


def to_csv(rows):
    lines = [",".join(COLUMNS)]
    for r in rows:
        lines.append(",".join(f'"{r[c]}"' for c in COLUMNS))
    return "\n".join(lines) + "\n"


def row(**overrides):
    return {**GOOD_ROW, **overrides}


# ---- active EU RoHS pack

def test_active_eu_rohs_pack_has_ten_substances():
    packs = load_active_packs()
    rohs = next(p for p in packs if p.pack_id == "eu_rohs")
    assert len(rohs.rules) == 10
    assert not rohs.is_subset
    assert {r.obligation_type for r in rohs.rules} == {"restriction"}


def test_eu_rohs_limits():
    limits = {r.substance: r.limit_pct for r in load_active_rules() if r.pack_id == "eu_rohs"}
    assert limits.pop("Cadmium") == 0.01
    assert set(limits.values()) == {0.1}


def test_rule_id_is_stable_citation():
    lead = next(r for r in load_active_rules() if r.substance == "Lead")
    assert lead.rule_id == f"eu_rohs@{lead.version_id}#lead"


# ---- validation: good files

def test_valid_csv_and_json_parse_to_same_rules():
    csv_pack = parse_pack_text(to_csv([row()]), "p.csv")
    json_pack = parse_pack_text(json.dumps([row(cas_numbers=["7439-92-1"], is_subset=False)]), "p.json")
    assert csv_pack.rules == json_pack.rules


def test_warning_row_allows_empty_limit():
    pack = parse_pack_text(to_csv([row(obligation_type="warning", limit_pct="")]), "p.csv")
    assert pack.rules[0].limit_pct is None


# ---- validation: rejection is whole-file with row-level errors

@pytest.mark.parametrize("bad, message", [
    (row(obligation_type="ban"), "obligation_type"),
    (row(limit_pct=""), "limit_pct is required"),
    (row(limit_pct="abc"), "not a number"),
    (row(limit_pct="0"), "must be > 0"),
    (row(obligation_type="warning", limit_pct="0.1"), "must be empty"),
    (row(cas_numbers="7439-92-2"), "check digit"),
    (row(cas_numbers="7439922"), "format"),
    (row(source_url="http://example.org"), "https://"),
    (row(version_date="01/01/2020"), "ISO date"),
    (row(last_verified="2019-01-01"), "before version_date"),
    (row(is_subset="yes"), "is_subset"),
    (row(pack_id="Bad-ID"), "pack_id"),
    (row(substance=""), "substance is required"),
    (row(verified_by=""), "verified_by is required"),
])
def test_bad_rows_rejected(bad, message):
    with pytest.raises(RulePackError) as exc:
        parse_pack_text(to_csv([row(), bad]), "p.csv")
    assert any(message in e and e.startswith("row 2") for e in exc.value.errors)


def test_one_bad_row_rejects_whole_file_and_reports_every_error():
    rows = [row(), row(substance="Mercury", cas_numbers="7439-97-6", limit_pct="x"),
            row(substance="Cadmium", cas_numbers="7440-43-9", is_subset="maybe")]
    with pytest.raises(RulePackError) as exc:
        parse_pack_text(to_csv(rows), "p.csv")
    assert [e.split(":")[0] for e in exc.value.errors] == ["row 2", "row 3"]


def test_duplicate_substance_and_cas_rejected():
    with pytest.raises(RulePackError) as exc:
        parse_pack_text(to_csv([row(), row(substance="lead", cas_numbers="")]), "p.csv")
    assert "duplicates row 1" in str(exc.value)
    with pytest.raises(RulePackError) as exc:
        parse_pack_text(to_csv([row(), row(substance="Lead metal")]), "p.csv")
    assert "already used on row 1" in str(exc.value)


def test_mixed_packs_in_one_file_rejected():
    with pytest.raises(RulePackError, match="one pack per file"):
        parse_pack_text(to_csv([row(), row(substance="Mercury", cas_numbers="", pack_id="other")]), "p.csv")


def test_header_must_match_schema_exactly():
    text = to_csv([row()]).replace("is_subset", "subset")
    with pytest.raises(RulePackError) as exc:
        parse_pack_text(text, "p.csv")
    assert "missing column(s): is_subset" in str(exc.value)
    assert "unknown column(s): subset" in str(exc.value)


def test_json_unknown_field_and_unsupported_type_rejected():
    with pytest.raises(RulePackError, match="unknown field"):
        parse_pack_text(json.dumps([{**row(), "aliases": "x"}]), "p.json")
    with pytest.raises(RulePackError, match="unsupported type"):
        parse_pack_text("x", "p.xlsx")
    with pytest.raises(RulePackError, match="no rule rows"):
        parse_pack_text(",".join(COLUMNS) + "\n", "p.csv")


def test_active_pointer_to_missing_version_fails(tmp_path):
    shutil.copytree(RULES_DIR, tmp_path / "rules")
    (tmp_path / "rules" / "active.json").write_text(
        '{"packs": {"eu_rohs": "20990101T000000Z"}, "synonyms": "20261010T000000Z"}')
    with pytest.raises(RulePackError, match="does not exist"):
        load_active_packs(tmp_path / "rules")


def test_cas_checksum():
    for cas in ("7439-92-1", "7440-43-9", "18540-29-9", "117-81-7", "1163-19-5"):
        assert cas_checksum_ok(cas)
    assert not cas_checksum_ok("7439-92-9")


def test_every_active_cas_number_passes_checksum():
    for r in load_active_rules():
        for cas in r.cas_numbers:
            assert cas_checksum_ok(cas), (r.substance, cas)


# ---- matching

@pytest.fixture(scope="module")
def matcher():
    return SubstanceMatcher(load_active_rules(), load_active_synonyms())


@pytest.mark.parametrize("name, cas, expected", [
    ("Lead (Pb)", None, "Lead"),
    ("Pb", None, "Lead"),
    ("lead ", None, "Lead"),
    ("Cr(VI)", None, "Hexavalent chromium"),
    ("Chromium VI", None, "Hexavalent chromium"),
    ("PBBs", None, "Polybrominated biphenyls (PBB)"),
    ("decaBDE", None, "Polybrominated diphenyl ethers (PBDE)"),
    ("Polybrominated diphenyl ethers (PBDEs)", None, "Polybrominated diphenyl ethers (PBDE)"),
    ("DEHP", None, "Bis(2-ethylhexyl) phthalate (DEHP)"),
    ("Diisobutyl phthalate", None, "Diisobutyl phthalate (DIBP)"),
    ("something odd", "CAS 7440-43-9", "Cadmium"),
    ("Hexabromobiphenyl", "36355-01-8", "Polybrominated biphenyls (PBB)"),
])
def test_matcher_hits(matcher, name, cas, expected):
    assert [r.substance for r in matcher.match(name, cas)] == [expected]


def test_matcher_misses_unrestricted(matcher):
    assert matcher.match("Copper", "7440-50-8") == []
    assert matcher.match(None, None) == []


# ---- synonyms: versioned and validated like rule packs

SYN_OK = {"last_verified": "2026-01-01", "verified_by": "Tester", "synonyms": {"Lead": ["Pb"], "Mercury": ["Hg"]}}


def test_active_synonyms_cover_every_eu_rohs_substance():
    syn = load_active_synonyms()
    assert syn.version_id
    for rule in load_active_rules():
        assert syn.aliases_for(rule.substance), rule.substance


def test_valid_synonyms_parse():
    syn = parse_synonyms_text(json.dumps(SYN_OK))
    assert syn.aliases_for("lead") == ("Pb",)


@pytest.mark.parametrize("data, message", [
    ({**SYN_OK, "synonyms": {"Lead": ["Pb"], "Mercury": ["pb"]}}, "already used by 'Lead'"),
    ({**SYN_OK, "synonyms": {"Lead": ["Pb"], "Mercury": ["Lead"]}}, "already used by 'Lead'"),
    ({**SYN_OK, "synonyms": {"Lead": "Pb"}}, "list of strings"),
    ({**SYN_OK, "synonyms": {"Lead": ["--"]}}, "empty after normalization"),
    ({**SYN_OK, "synonyms": {}}, "non-empty"),
    ({**SYN_OK, "last_verified": "yesterday"}, "ISO date"),
    ({**SYN_OK, "verified_by": " "}, "verified_by is required"),
    ({**SYN_OK, "limits": {}}, "unknown field"),
    ({k: v for k, v in SYN_OK.items() if k != "verified_by"}, "missing field"),
])
def test_bad_synonyms_rejected(data, message):
    with pytest.raises(RulePackError, match=message):
        parse_synonyms_text(json.dumps(data))


def test_active_pointer_to_missing_synonyms_fails(tmp_path):
    shutil.copytree(RULES_DIR, tmp_path / "rules")
    (tmp_path / "rules" / "active.json").write_text(
        '{"packs": {"eu_rohs": "20261010T000000Z"}, "synonyms": "20990101T000000Z"}')
    with pytest.raises(RulePackError, match="does not exist"):
        load_active_synonyms(tmp_path / "rules")
