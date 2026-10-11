"""Extraction: schema validation, consistency checks, one retry, disk cache. No network."""

import json

import pytest

from radar import cache
from radar.documents import load_document
from radar.extract import (
    EXTRACTION_VERSION,
    MODEL,
    ExtractionError,
    extract,
    grounding_warnings,
    validate_extraction,
)
from tests.fakes import FakeClient, response

DOC_TEXT = "Supplier: Testco (fictional)\n| Lead | 7439-92-1 | 450 | ppm |\n| Cadmium | 7440-43-9 | trace amounts | |"


def substance(**over):
    base = {
        "name": "Lead", "cas": "7439-92-1", "concentration_raw": "450", "concentration_value": 450,
        "unit": "ppm", "exemption_claimed": False, "exemption_text": None,
        "source_text": "| Lead | 7439-92-1 | 450 | ppm |",
    }
    return {**base, **over}


def payload(*subs):
    return {"supplier": "Testco (fictional)", "component": "Widget", "substances": list(subs) or [substance()]}


@pytest.fixture
def doc():
    return load_document("test.txt", DOC_TEXT.encode())


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


# ---- validation

def test_valid_payload_passes():
    data, errors = validate_extraction(json.dumps(payload()))
    assert errors == [] and data["substances"][0]["name"] == "Lead"


def test_vague_value_is_valid_with_null_number_and_unit():
    vague = substance(name="Cadmium", concentration_raw="trace amounts", concentration_value=None, unit=None)
    assert validate_extraction(json.dumps(payload(vague)))[1] == []


@pytest.mark.parametrize("bad, message", [
    ("not json", "not valid JSON"),
    (json.dumps({"supplier": "x", "substances": []}), "'component' is a required property"),
    (json.dumps(payload(substance(unit="percent"))), "is not valid"),
    (json.dumps(payload(substance(extra=1))), "Additional properties"),
    (json.dumps(payload(substance(concentration_value=0.045))), "does not appear in concentration_raw"),
    (json.dumps(payload(substance(concentration_value=None, unit=None))), "contains a number"),
    (json.dumps(payload(substance(unit=None))), "unit is null"),
    (json.dumps(payload(substance(concentration_raw="N/A", concentration_value=None))), "unit must be null"),
    (json.dumps(payload(substance(exemption_claimed=True))), "exemption_text is empty"),
    (json.dumps(payload(substance(source_text="  "))), "source_text is empty"),
])
def test_invalid_payloads_rejected(bad, message):
    data, errors = validate_extraction(bad)
    assert data is None
    assert any(message in e for e in errors), errors


def test_grounding_warning_for_invented_source_text():
    data = payload(substance(source_text="Lead: 450 ppm per our lab"))
    assert grounding_warnings(data, DOC_TEXT) == ["Source text for 'Lead' was not found verbatim in the document."]
    assert grounding_warnings(payload(), DOC_TEXT) == []


# ---- extraction flow

def test_extract_success_first_try_and_cached(doc, cache_dir):
    client = FakeClient(response(payload()))
    result = extract(doc, client=client, cache_dir=cache_dir)
    assert result.attempts == 1 and not result.from_cache
    assert result.data["substances"][0]["concentration_value"] == 450
    assert len(client.calls) == 1

    call = client.calls[0]
    assert call["model"] == MODEL == "claude-sonnet-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert DOC_TEXT in call["messages"][0]["content"]

    # Second run: served from disk, the client is never touched.
    again = extract(doc, client=FakeClient(), cache_dir=cache_dir)
    assert again.from_cache and again.data == result.data


def test_retry_once_with_validation_errors(doc, cache_dir):
    bad = payload(substance(concentration_value=0.045))  # model "converted" ppm to %
    client = FakeClient(response(bad), response(payload()))
    result = extract(doc, client=client, cache_dir=cache_dir)
    assert result.attempts == 2
    assert len(client.calls) == 2
    retry_messages = client.calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert "does not appear in concentration_raw" in retry_messages[2]["content"]


def test_second_failure_raises_and_is_not_cached(doc, cache_dir):
    client = FakeClient(response("not json"), response("still not json"))
    with pytest.raises(ExtractionError, match="still invalid after one retry"):
        extract(doc, client=client, cache_dir=cache_dir)
    assert len(client.calls) == 2
    assert cache.get(cache.file_hash(doc.data), EXTRACTION_VERSION, MODEL, cache_dir=cache_dir) is None


@pytest.mark.parametrize("stop, message", [("refusal", "declined"), ("max_tokens", "ran out")])
def test_refusal_and_truncation_raise(doc, cache_dir, stop, message):
    with pytest.raises(ExtractionError, match=message):
        extract(doc, client=FakeClient(response(payload(), stop_reason=stop)), cache_dir=cache_dir)


def test_changed_file_misses_cache(doc, cache_dir):
    extract(doc, client=FakeClient(response(payload())), cache_dir=cache_dir)
    edited = load_document("test.txt", (DOC_TEXT + "\n").encode())
    client = FakeClient(response(payload()))
    assert not extract(edited, client=client, cache_dir=cache_dir).from_cache
    assert len(client.calls) == 1


def test_missing_api_key_gives_clear_error(doc, cache_dir, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY is not set"):
        extract(doc, cache_dir=cache_dir)


def test_cache_entry_never_contains_the_api_key(doc, cache_dir, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    extract(doc, client=FakeClient(response(payload())), cache_dir=cache_dir)
    for path in cache_dir.glob("*.json"):
        assert "sk-test" not in path.read_text()


def test_network_is_blocked_in_tests():
    import socket

    with pytest.raises(RuntimeError, match="blocked"):
        socket.create_connection(("api.anthropic.com", 443))
