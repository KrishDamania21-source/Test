"""Agent output tests: guard checks, retry, cache. No network (conftest.py blocks it)."""

import pytest

from radar.agent import (
    AGENT_OUTPUT_VERSION,
    MODEL,
    NO_EMAIL,
    AgentError,
    AgentOutput,
    check_near_limit_email,
    check_numbers,
    check_signature,
    check_substances,
    draft,
)
from radar.engine import (
    HIGH,
    LOW,
    MEDIUM,
    MISSING_CAS,
    NEAR_LIMIT,
    NOT_DECLARED,
    OVER_LIMIT,
    Finding,
    ScreeningResult,
)
from radar.rules import load_active_rules, load_active_synonyms
from tests.fakes import FakeClient, response

RULES = load_active_rules()
SYNONYMS = load_active_synonyms()

# A restriction rule from the active pack (used to build test ScreeningResults)
_LEAD_RULE = next(r for r in RULES if r.substance == "Lead")
_MERCURY_RULE = next(r for r in RULES if r.substance == "Mercury")
_CADMIUM_RULE = next(r for r in RULES if r.substance == "Cadmium")


# --------------------------------------------------------------------------- helpers

def _over_limit_finding(substance="Lead", rule=None, value_pct=0.35) -> Finding:
    r = rule or _LEAD_RULE
    return Finding(
        type=OVER_LIMIT,
        severity=HIGH,
        substance=substance,
        message=f"{substance}: {value_pct:.6g}% is above the {r.limit_pct:.6g}% limit.",
        rule=r,
        extracted_name=substance,
        cas=r.cas_numbers[0] if r.cas_numbers else None,
        value_pct=value_pct,
        raw_value=str(value_pct),
        source_text=f"| {substance} | {value_pct} | % |",
    )


def _near_limit_finding(substance="Cadmium", rule=None, value_pct=0.009) -> Finding:
    r = rule or _CADMIUM_RULE
    pct_of_limit = value_pct / r.limit_pct
    return Finding(
        type=NEAR_LIMIT,
        severity=LOW,
        substance=substance,
        message=(
            f"{substance}: {value_pct:.6g}% is within 80% of the {r.limit_pct:.6g}% limit "
            f"({pct_of_limit:.0%} of the limit)."
        ),
        rule=r,
        extracted_name=substance,
        cas=r.cas_numbers[0] if r.cas_numbers else None,
        value_pct=value_pct,
        raw_value=str(value_pct),
        source_text=f"| {substance} | {value_pct} | % |",
    )


def _not_declared_finding(substance="Mercury", rule=None) -> Finding:
    r = rule or _MERCURY_RULE
    return Finding(
        type=NOT_DECLARED,
        severity="Info",
        substance=substance,
        message=f"{substance}: not addressed in the declaration. Please confirm.",
        rule=r,
    )


def _screening(findings, rating=HIGH, needs_review=False, watch=False) -> ScreeningResult:
    return ScreeningResult(
        supplier="Kestrel Microparts (fictional)",
        component="PCB Assembly Rev 2",
        findings=findings,
        rating=rating,
        needs_review=needs_review,
        watch=watch,
    )


def _ok_response(substance="Lead", value="0.35", limit="0.1") -> str:
    return (
        f"EXPLANATION:\n"
        f"{substance} was found at {value}%, which exceeds the EU RoHS limit of {limit}%. "
        f"This rates High and requires action from the supplier.\n\n"
        f"EMAIL:\n"
        f"DRAFT – for review before sending\n\n"
        f"Subject: EU RoHS compliance — {substance} over limit\n\n"
        f"Dear Kestrel Microparts,\n\n"
        f"Your declaration lists {substance} at {value}%, above the {limit}% limit. "
        f"Please provide supporting evidence or a corrected declaration.\n\n"
        f"Please send your response at your earliest convenience.\n\n"
        f"[Your name]\nSupplier Compliance"
    )


# --------------------------------------------------------------------------- guard unit tests

def test_substance_guard_catches_invented_substance():
    result = _screening([_over_limit_finding("Lead")])
    # "Mercury" is in the rules but not in these findings → forbidden
    errors = check_substances("Mercury exceeds the limit.", result, RULES)
    assert any("mercury" in e.lower() for e in errors)


def test_substance_guard_allows_finding_substances():
    result = _screening([_over_limit_finding("Lead")])
    errors = check_substances("Lead was found at 0.35%.", result, RULES)
    assert errors == []


def test_substance_guard_allows_not_declared():
    result = _screening([_not_declared_finding("Mercury")])
    errors = check_substances("Please confirm Mercury.", result, RULES)
    assert errors == []


def test_number_guard_catches_wrong_value():
    result = _screening([_over_limit_finding("Lead", value_pct=0.35)])
    # "0.99" does not appear in any finding
    errors = check_numbers("Lead was found at 0.99%.", result, RULES)
    assert any("0.99" in e for e in errors)


def test_number_guard_allows_finding_values():
    result = _screening([_over_limit_finding("Lead", value_pct=0.35)])
    # "0.35" and "0.1" come from the finding message
    errors = check_numbers("Lead at 0.35%, limit is 0.1%.", result, RULES)
    assert errors == []


def test_number_guard_allows_rule_limits():
    # Rule limits (e.g. 0.01 for Cadmium) should always be allowed even for NOT_DECLARED
    cadmium_rule = next(r for r in RULES if r.substance == "Cadmium")
    result = _screening([_not_declared_finding("Mercury")])
    limit_str = str(cadmium_rule.limit_pct)
    errors = check_numbers(f"Please confirm below {limit_str}% limit.", result, RULES)
    assert errors == []


# --------------------------------------------------------------------------- draft() integration

def test_draft_returns_from_cache_on_second_call(tmp_path):
    findings = [_over_limit_finding()]
    result = _screening(findings)
    client = FakeClient(response(_ok_response()))

    out1 = draft(result, RULES, client=client, cache_dir=tmp_path)
    assert not out1.from_cache
    assert out1.attempts == 1
    assert "Lead" in out1.explanation

    # Second call: no client needed, should come from cache
    out2 = draft(result, RULES, client=None, cache_dir=tmp_path)
    assert out2.from_cache
    assert out2.explanation == out1.explanation


def test_draft_retries_on_guard_failure(tmp_path):
    """First response invents a forbidden substance; second response is clean."""
    findings = [_over_limit_finding("Lead")]
    result = _screening(findings)

    # First response invents "Mercury" (not in findings → forbidden)
    bad = (
        "EXPLANATION:\nLead and Mercury both exceeded limits.\n\n"
        "EMAIL:\nSubject: issues\n\nDear supplier,\nLead and Mercury problems.\n\n"
        "RADAR Compliance System (prototype — synthetic data only)"
    )
    good = _ok_response()
    client = FakeClient(response(bad), response(good))

    out = draft(result, RULES, client=client, cache_dir=tmp_path)
    assert out.attempts == 2
    assert out.error is None
    assert "Mercury" not in out.explanation
    assert len(client.calls) == 2


def test_draft_sets_error_when_guard_fails_twice(tmp_path):
    """Both attempts invent a forbidden substance; error is set, no text returned."""
    findings = [_over_limit_finding("Lead")]
    result = _screening(findings)

    bad = (
        "EXPLANATION:\nLead and Mercury both exceeded limits.\n\n"
        "EMAIL:\nSubject: issues\n\nDear supplier,\nProblems with Lead and Mercury.\n\n"
        "RADAR Compliance System (prototype — synthetic data only)"
    )
    client = FakeClient(response(bad), response(bad))

    out = draft(result, RULES, client=client, cache_dir=tmp_path)
    assert out.attempts == 2
    assert out.error is not None
    assert out.explanation == ""
    assert out.email == ""


def test_draft_no_findings_returns_no_email_without_api(tmp_path):
    result = _screening([], rating=LOW)
    out = draft(result, RULES, client=None, cache_dir=tmp_path)
    assert out.email == NO_EMAIL
    assert out.attempts == 0
    assert out.error is None
    assert out.explanation  # some text should be present


def test_draft_trace_fn_called(tmp_path):
    findings = [_over_limit_finding()]
    result = _screening(findings)
    client = FakeClient(response(_ok_response()))
    events = []
    draft(result, RULES, client=client, trace_fn=events.append, cache_dir=tmp_path)
    assert "Drafting follow-up" in events


def test_draft_trace_fn_not_called_for_no_findings(tmp_path):
    result = _screening([], rating=LOW)
    events = []
    draft(result, RULES, client=None, trace_fn=events.append, cache_dir=tmp_path)
    assert "Drafting follow-up" not in events


def test_draft_missing_explanation_section_raises(tmp_path):
    findings = [_over_limit_finding()]
    result = _screening(findings)
    bad_format = "Here is some text without the right headings."
    client = FakeClient(response(bad_format))
    with pytest.raises(AgentError, match="EXPLANATION"):
        draft(result, RULES, client=client, cache_dir=tmp_path)


# --------------------------------------------------------------------------- new guard tests

def test_near_limit_guard_catches_correct_word():
    result = _screening([_near_limit_finding()], rating=LOW, watch=True)
    errors = check_near_limit_email(
        "Please provide a corrected declaration for cadmium.", result
    )
    assert any("correct" in e.lower() for e in errors)


def test_near_limit_guard_catches_80_percent():
    result = _screening([_near_limit_finding()], rating=LOW, watch=True)
    errors = check_near_limit_email(
        "The value is at 80% of the legal limit.", result
    )
    assert any("80%" in e for e in errors)


def test_near_limit_guard_does_not_fire_for_over_limit():
    """OVER_LIMIT findings should not trigger the NEAR_LIMIT guard."""
    result = _screening([_over_limit_finding()])
    errors = check_near_limit_email("Please provide a corrected declaration.", result)
    assert errors == []


def test_near_limit_guard_allows_clean_email():
    result = _screening([_near_limit_finding()], rating=LOW, watch=True)
    clean = (
        "DRAFT – for review before sending\n\n"
        "Subject: Cadmium near limit\n\n"
        "Dear Supplier,\n\n"
        "Please provide the test report and analytical method.\n\n"
        "[Your name]\nSupplier Compliance"
    )
    assert check_near_limit_email(clean, result) == []


def test_signature_guard_catches_radar_compliance_system():
    email = (
        "DRAFT – for review before sending\n\n"
        "Subject: test\n\nDear supplier,\n\nPlease act.\n\n"
        "RADAR Compliance System (prototype — synthetic data only)"
    )
    assert check_signature(email)


def test_signature_guard_allows_human_name():
    email = (
        "DRAFT – for review before sending\n\n"
        "Subject: test\n\nDear supplier,\n\nPlease act.\n\n"
        "[Your name]\nSupplier Compliance"
    )
    assert check_signature(email) == []


def test_no_email_passes_signature_guard():
    assert check_signature(NO_EMAIL) == []
