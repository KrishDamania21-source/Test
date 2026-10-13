"""Agent output tests: guard checks, retry, cache. No network (conftest.py blocks it)."""

import pytest

from radar.agent import (
    AGENT_OUTPUT_VERSION,
    MODEL,
    NO_EMAIL,
    AgentError,
    AgentOutput,
    check_numbers,
    check_substances,
    draft,
)
from radar.engine import (
    HIGH,
    LOW,
    MEDIUM,
    MISSING_CAS,
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
        f"This rates High and requires a corrected declaration.\n\n"
        f"EMAIL:\n"
        f"Subject: EU RoHS compliance — {substance} over limit\n\n"
        f"Dear Kestrel Microparts,\n\n"
        f"Your declaration lists {substance} at {value}%, above the {limit}% limit. "
        f"Please provide a corrected declaration with a measured concentration in %%.\n\n"
        f"RADAR Compliance System (prototype — synthetic data only)"
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
