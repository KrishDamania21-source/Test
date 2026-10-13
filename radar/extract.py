"""Step 3: Claude reads a declaration and returns structured JSON.

The model reads; it never judges. It reports what the document says
(names, CAS numbers, values exactly as written, units, exemption claims,
and the source text) and nothing else. Unit conversion and every
compliance decision happen in Python.

Flow per document:
  1. Hash the file bytes. A cache hit returns immediately, with no API call.
  2. Call Claude with a JSON schema for the output.
  3. Validate: JSON parses, matches the schema, and passes consistency
     checks (a reported number must actually appear in the raw value).
     Whether each source_text really appears in the document is judged
     later by the rules engine (UNTRACEABLE_EXTRACTION), not here.
  4. If validation fails, retry once, sending the errors back.
  5. Cache the validated result on disk.

The API key is read by the SDK from the ANTHROPIC_API_KEY environment
variable. This module never reads, prints or logs it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

from radar import cache
from radar.documents import Document
from radar.units import TO_PERCENT, numbers_in

MODEL = "claude-sonnet-5-5"
# Bump when the prompt or schema changes: old cache entries then stop matching.
EXTRACTION_VERSION = "x1"
MAX_TOKENS = 16000
EFFORT = "medium"

UNITS = list(TO_PERCENT) + ["unknown"]

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier": _NULLABLE_STRING,
        "component": _NULLABLE_STRING,
        "substances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "cas": _NULLABLE_STRING,
                    "concentration_raw": _NULLABLE_STRING,
                    "concentration_value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "unit": {"anyOf": [{"type": "string", "enum": UNITS}, {"type": "null"}]},
                    "exemption_claimed": {"type": "boolean"},
                    "exemption_text": _NULLABLE_STRING,
                    "source_text": {"type": "string"},
                },
                "required": [
                    "name", "cas", "concentration_raw", "concentration_value", "unit",
                    "exemption_claimed", "exemption_text", "source_text",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["supplier", "component", "substances"],
    "additionalProperties": False,
}

_VALIDATOR = Draft202012Validator(EXTRACTION_SCHEMA)

SYSTEM_PROMPT = """You extract data from supplier material declarations. You are a careful reader, not a judge.

Return one JSON object with the supplier name, the component, and every substance the document lists with a concentration or a statement about it.

For each substance:
- name: the substance name exactly as written.
- cas: the CAS number exactly as written, or null if the document gives none for that substance. Never supply a CAS number from your own knowledge.
- concentration_raw: the concentration exactly as written, including any qualifier or words (for example "0.35", "<0.001", "450", "trace amounts", "N/A", "<LOD"). Use null only when no value is given at all, such as an empty cell shown as (blank).
- concentration_value: the plain number inside concentration_raw, unchanged. Use null if concentration_raw contains no number. Never convert units, never round, never calculate.
- unit: the unit that applies to the value: "%", "ppm" or "mg/kg". Treat "% w/w", "wt%" and "% by weight" as "%". Take the unit from a column header when the cell itself has none. Use "unknown" if a number has no identifiable unit. Use null when concentration_value is null.
- exemption_claimed: true only if the document explicitly claims an exemption for this substance.
- exemption_text: the exemption wording exactly as written, or null.
- source_text: the line, table row or sentence the substance came from, copied character for character from the document.

Rules:
- Report only what the document says. Do not add substances the document does not mention.
- Do not decide whether anything is compliant, over a limit, or acceptable. Do not comment.
- If the supplier or component is not stated, use null."""


class ExtractionError(RuntimeError):
    pass


@dataclass
class ExtractionResult:
    filename: str
    file_sha256: str
    model: str
    extraction_version: str
    data: dict
    attempts: int
    from_cache: bool
    document_text: str = ""
    usage: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        return {
            "filename": self.filename,
            "file_sha256": self.file_sha256,
            "model": self.model,
            "extraction_version": self.extraction_version,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "attempts": self.attempts,
            "usage": self.usage,
            "data": self.data,
            # Kept so findings can be re-screened later (new rule packs) with no API call.
            "document_text": self.document_text,
        }


# ---------------------------------------------------------------- validation

def validate_extraction(text: str) -> tuple[dict | None, list[str]]:
    """Return (data, errors). Errors are hard failures that trigger the retry."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [f"Output is not valid JSON: {e}"]

    errors = [
        f"{'/'.join(str(p) for p in err.absolute_path) or '(root)'}: {err.message}"
        for err in sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return None, errors

    for i, s in enumerate(data["substances"]):
        where = f"substances/{i} ({s['name']})"
        value, raw, unit = s["concentration_value"], s["concentration_raw"], s["unit"]
        found = numbers_in(raw)
        if value is not None and not any(abs(n - value) <= 1e-12 * max(1.0, abs(value)) for n in found):
            errors.append(f"{where}: concentration_value {value} does not appear in concentration_raw "
                          f"{raw!r}. Copy the number as written; do not convert.")
        if value is None and found:
            errors.append(f"{where}: concentration_raw {raw!r} contains a number but concentration_value is null.")
        if value is not None and unit is None:
            errors.append(f"{where}: unit is null but a value is given. Use \"unknown\" if no unit is stated.")
        if value is None and unit is not None:
            errors.append(f"{where}: unit must be null when concentration_value is null.")
        if s["exemption_claimed"] and not (s["exemption_text"] or "").strip():
            errors.append(f"{where}: exemption_claimed is true but exemption_text is empty.")
        if not s["source_text"].strip():
            errors.append(f"{where}: source_text is empty.")
    return (data if not errors else None), errors


# ---------------------------------------------------------------- API call

_KEY_FILE = os.path.expanduser("~/.config/radar/api_key")


def make_client():
    """Anthropic client.

    Reads the API key from ANTHROPIC_API_KEY if set, otherwise from
    ~/.config/radar/api_key. The key is never printed or logged.
    """
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            with open(_KEY_FILE) as fh:
                key = fh.read().strip()
        except OSError:
            pass
    if not key:
        raise ExtractionError(
            "ANTHROPIC_API_KEY is not set and ~/.config/radar/api_key is missing or empty."
        )
    return anthropic.Anthropic(api_key=key)


def _user_prompt(doc: Document) -> str:
    return (f"Declaration file: {doc.filename} (type: {doc.kind})\n\n"
            f"<document>\n{doc.text}\n</document>\n\n"
            "Extract the declaration as JSON.")


def _block_to_param(block):
    to_dict = getattr(block, "to_dict", None)
    return to_dict() if callable(to_dict) else block


def _call(client, messages: list) -> object:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
        },
    )
    # No fallback model: every result comes from MODEL. A refusal is an extraction error.
    if response.stop_reason == "refusal":
        raise ExtractionError(f"{MODEL} declined to process this document (refusal).")
    if not str(getattr(response, "model", MODEL)).startswith(MODEL):
        raise ExtractionError(f"Response came from {response.model}, expected {MODEL}.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The model ran out of output tokens before finishing.")
    return response


def _text_of(response) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()


def _add_usage(total: dict, response) -> None:
    usage = getattr(response, "usage", None)
    for key in ("input_tokens", "output_tokens"):
        total[key] = total.get(key, 0) + (getattr(usage, key, 0) or 0)


def extract(doc: Document, client=None, use_cache: bool = True, cache_dir=None) -> ExtractionResult:
    sha = cache.file_hash(doc.data)
    cache_kwargs = {"cache_dir": cache_dir} if cache_dir else {}

    if use_cache:
        hit = cache.get(sha, EXTRACTION_VERSION, MODEL, **cache_kwargs)
        if hit:
            return ExtractionResult(
                filename=doc.filename, file_sha256=sha, model=MODEL,
                extraction_version=EXTRACTION_VERSION, data=hit["data"], attempts=hit.get("attempts", 1),
                from_cache=True, document_text=hit.get("document_text", doc.text), usage=hit.get("usage", {}),
            )

    client = client or make_client()
    usage: dict = {}
    messages = [{"role": "user", "content": _user_prompt(doc)}]

    response = _call(client, messages)
    _add_usage(usage, response)
    data, errors = validate_extraction(_text_of(response))
    attempts = 1

    if errors:
        attempts = 2
        messages += [
            {"role": "assistant", "content": [_block_to_param(b) for b in response.content]},
            {"role": "user", "content": "Your JSON failed validation:\n- " + "\n- ".join(errors)
                + "\n\nReturn the corrected JSON for the same document."},
        ]
        response = _call(client, messages)
        _add_usage(usage, response)
        data, errors = validate_extraction(_text_of(response))
        if errors:
            raise ExtractionError(f"{doc.filename}: extraction still invalid after one retry: " + "; ".join(errors))

    result = ExtractionResult(
        filename=doc.filename, file_sha256=sha, model=MODEL, extraction_version=EXTRACTION_VERSION,
        data=data, attempts=attempts, from_cache=False,
        document_text=doc.text, usage=usage,
    )
    cache.put(sha, EXTRACTION_VERSION, MODEL, result.to_record(), **cache_kwargs)
    return result

