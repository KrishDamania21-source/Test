"""Step 5: Claude writes explanation and follow-up email from findings only.

The model never sees the original document. It receives only a structured
summary of the rules-engine findings and writes:
  - a 2–3 sentence plain-English risk explanation
  - a draft follow-up email to the supplier

Guard checks (deterministic Python, not model judgment):
  - Every restricted-substance name in the output must appear in the
    findings. A substance invented by the model is caught here.
  - Every decimal number quoted must appear in the findings (messages,
    raw values, or rule limits). A misquoted value is caught here.

On a guard failure the response is regenerated once with the error
list fed back to the model. If it fails again, AgentOutput.error is
set and no text is shown.

Outputs are cached by a hash of the findings plus AGENT_OUTPUT_VERSION,
so re-running the pipeline never re-calls the API for identical findings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from radar import cache
from radar.engine import NOT_DECLARED, ScreeningResult
from radar.extract import MODEL, make_client
from radar.rules import Rule

AGENT_OUTPUT_VERSION = "a2"
MAX_TOKENS = 2048

NO_EMAIL = "No follow-up needed."

AGENT_CACHE_DIR = Path(os.environ.get(
    "RADAR_AGENT_CACHE_DIR",
    Path(__file__).resolve().parent.parent / ".cache" / "agent_outputs",
))

SYSTEM_PROMPT = """You are a compliance notice writer. Your only source of information is the
structured findings summary provided below. Never introduce substance names, numeric
values, legal citations, or conclusions that are not present in the findings.

Write two sections:

EXPLANATION:
Two to three sentences of plain-English risk summary for an internal compliance team.
Name the specific substances and issues from the findings. Be direct about the risk
rating.
- For NEAR_LIMIT findings: describe the value as "at X% of the limit, above the 80%
  watch threshold" (take the percentage from the finding message). State clearly that
  the value is compliant.
- If rating is Low with no actionable findings, one sentence confirming the review is
  enough.

EMAIL:
Start the email body with this exact line on its own:
DRAFT – for review before sending

Then write the professional follow-up. Follow these rules for each finding type:

OVER_LIMIT — offer three numbered paths:
(a) If the declared value is incorrect, please provide a corrected declaration stating
    the measured concentration in % and the CAS registry number.
(b) If a regulatory exemption applies to this use, please provide the exemption
    justification and confirmation that it is valid for this product category and is
    still current.
(c) If the value is correct and no exemption applies, please provide a compliant
    replacement or a corrective action plan with an expected timeline.

NEAR_LIMIT — the declared value is within the legal limit. Never use the word
"correct" or any form of it. Never mention any 80% threshold. Ask for: the test
report and analytical method used to determine the value, and whether batch or
process variation could cause it to exceed the legal limit in future production.

UNVERIFIABLE — match the response to the raw value shown in the finding:
- "<LOD" or any value starting with "<" with no number following: ask for the limit
  of detection and the analytical test method. Note that a stated LOD below the legal
  limit is acceptable evidence.
- "N/A": ask whether the substance is absent from the component or was simply not
  tested.
- "trace amounts" or any other qualitative description: ask for a measured
  concentration in % and the analytical method used.

EXEMPTION_CLAIMED — do not ask for a corrected declaration. Ask for the exemption
justification document and confirmation that the exemption is valid for this product
category and is still current.

MISSING_CONCENTRATION — ask for a corrected declaration stating the measured
concentration in %.

MISSING_CAS — ask for a corrected declaration that includes the CAS registry number.

NOT_DECLARED section — if any substances are NOT_DECLARED, add a section headed
"Please confirm the following substances:" and use this one-line instruction before
the list: "For each substance below, please provide either a measured concentration
in % or a written statement that it is absent from the component."

Word choice: use "corrected" only when a value is missing (MISSING_CONCENTRATION) or
listed but invalid. Use "confirm" or "supporting evidence" in all other cases.

End every email with one sentence summarising what to send back, then close as:

[Your name]
Supplier Compliance

Never sign as RADAR, as a system, or with any tool or product name.

If there are no actionable findings and no NOT_DECLARED substances, write only the
words "No follow-up needed." as the entire EMAIL section.

Format your response exactly as:

EXPLANATION:
<text>

EMAIL:
<text>"""


# --------------------------------------------------------------------------- errors / output

class AgentError(RuntimeError):
    pass


@dataclass
class AgentOutput:
    supplier: str | None
    component: str | None
    rating_label: str
    explanation: str
    email: str          # NO_EMAIL constant or the draft email text
    from_cache: bool
    attempts: int       # 0 = no API call (no findings), 1 or 2 with guard retry
    error: str | None = None   # set when guard checks fail on both attempts


# --------------------------------------------------------------------------- cache key

def _findings_hash(result: ScreeningResult) -> str:
    data = [
        {
            "type": f.type,
            "substance": f.substance,
            "message": f.message,
            "value_pct": f.value_pct,
            "raw_value": f.raw_value,
        }
        for f in result.findings
    ]
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


# --------------------------------------------------------------------------- guard helpers

_DECIMAL_RE = re.compile(r"\d+\.\d+")


def _decimal_numbers(text: str | None) -> set[str]:
    if not text:
        return set()
    return {m.group() for m in _DECIMAL_RE.finditer(text)}


def _allowed_numbers(result: ScreeningResult, rules: list[Rule]) -> set[str]:
    """All decimal numbers the model is allowed to quote: from finding messages,
    raw values, and every rule limit."""
    nums: set[str] = set()
    for f in result.findings:
        nums |= _decimal_numbers(f.message)
        nums |= _decimal_numbers(f.raw_value)
    for r in rules:
        if r.limit_pct is not None:
            nums |= _decimal_numbers(str(r.limit_pct))
    return nums


def check_substances(text: str, result: ScreeningResult, rules: list[Rule]) -> list[str]:
    """Return error strings for any rule-substance name not in findings that appears in text."""
    allowed = {f.substance.lower() for f in result.findings}
    allowed |= {f.extracted_name.lower() for f in result.findings if f.extracted_name}
    all_rule_subs = {r.substance.lower() for r in rules}
    forbidden = all_rule_subs - allowed

    text_lower = text.lower()
    return [
        f"Substance \"{sub}\" appears in the output but is not in the findings."
        for sub in sorted(forbidden)
        if sub in text_lower
    ]


def check_numbers(text: str, result: ScreeningResult, rules: list[Rule]) -> list[str]:
    """Return error strings for any decimal number in text not in the allowed set."""
    allowed = _allowed_numbers(result, rules)
    if not allowed:
        return []
    return [
        f"Value {n!r} appears in the output but was not found in the findings or rule limits."
        for n in _decimal_numbers(text)
        if n not in allowed
    ]


def check_near_limit_email(email: str, result: ScreeningResult) -> list[str]:
    """For NEAR_LIMIT findings: email must not contain 'correct' or any 80% reference."""
    from radar.engine import NEAR_LIMIT
    if not any(f.type == NEAR_LIMIT for f in result.findings):
        return []
    errors = []
    if re.search(r"\bcorrect", email, re.IGNORECASE):
        errors.append(
            "NEAR_LIMIT email contains 'correct' — the value is compliant; "
            "do not ask for a correction."
        )
    if re.search(r"80\s*%", email):
        errors.append(
            "NEAR_LIMIT email contains '80%' — the internal watch threshold "
            "must not appear in the email."
        )
    return errors


def check_signature(email: str) -> list[str]:
    """Email must not be signed as RADAR or as a system."""
    if re.search(r"RADAR\s+Compliance\s+System", email, re.IGNORECASE):
        return [
            "Email is signed as 'RADAR Compliance System' — sign as "
            "'[Your name] / Supplier Compliance' instead."
        ]
    return []


def _guard(explanation: str, email: str, result: ScreeningResult, rules: list[Rule]) -> list[str]:
    combined = explanation + "\n" + email
    return (
        check_substances(combined, result, rules)
        + check_numbers(combined, result, rules)
        + check_near_limit_email(email, result)
        + check_signature(email)
    )


# --------------------------------------------------------------------------- prompt building

def _build_user_message(result: ScreeningResult) -> str:
    lines = [
        f"Supplier: {result.supplier or '(not stated)'}",
        f"Component: {result.component or '(not stated)'}",
        f"Risk rating: {result.label}",
        "",
        f"FINDINGS ({len(result.findings)} total):",
    ]
    for f in result.findings:
        lines.append("")
        lines.append(f"[{f.type} / {f.severity}] {f.substance}")
        lines.append(f"  {f.message}")
        if f.raw_value:
            lines.append(f"  Raw value in declaration: \"{f.raw_value}\"")
        if f.source_text and f.type != NOT_DECLARED:
            snippet = f.source_text[:120]
            lines.append(f"  Source text: \"{snippet}\"")
    if not result.findings:
        lines.append("  (none — all substances within limits and fully declared)")
    lines.append("")
    lines.append("Write the EXPLANATION and EMAIL now.")
    return "\n".join(lines)


def _parse_response(text: str) -> tuple[str, str]:
    """Extract EXPLANATION and EMAIL sections. Raises ValueError on bad format."""
    expl = re.search(
        r"EXPLANATION:\s*\n(.*?)(?=\n\s*EMAIL:|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    eml = re.search(r"EMAIL:\s*\n(.*)", text, re.DOTALL | re.IGNORECASE)
    if not expl:
        raise ValueError("Response is missing the EXPLANATION: section.")
    explanation = expl.group(1).strip()
    email = eml.group(1).strip() if eml else NO_EMAIL
    return explanation, email


# --------------------------------------------------------------------------- API call

def _call_once(client, messages: list) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    if response.stop_reason == "refusal":
        raise AgentError(f"{MODEL} refused to generate output.")
    if response.stop_reason == "max_tokens":
        raise AgentError("The model ran out of output tokens.")
    if not str(getattr(response, "model", MODEL)).startswith(MODEL):
        raise AgentError(
            f"Response came from {response.model!r}, expected {MODEL!r}."
        )
    return "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    ).strip()


# --------------------------------------------------------------------------- public API

def draft(
    result: ScreeningResult,
    rules: list[Rule],
    client=None,
    trace_fn=None,
    cache_dir: Path | None = None,
) -> AgentOutput:
    """Generate explanation and follow-up email from a ScreeningResult.

    Args:
        result:    the rules-engine output for one declaration.
        rules:     the active rule list (used only by the guard checks).
        client:    Anthropic client; created via make_client() if None.
        trace_fn:  optional callable(str) for live trace events.
        cache_dir: override the default agent-output cache directory.
    """
    effective_cache_dir = cache_dir or AGENT_CACHE_DIR
    sha = _findings_hash(result)

    hit = cache.get(sha, AGENT_OUTPUT_VERSION, MODEL, cache_dir=effective_cache_dir)
    if hit:
        return AgentOutput(
            supplier=result.supplier,
            component=result.component,
            rating_label=result.label,
            explanation=hit["explanation"],
            email=hit["email"],
            from_cache=True,
            attempts=hit.get("attempts", 1),
            error=hit.get("error"),
        )

    # No findings: return a canned result without an API call.
    if not result.findings:
        explanation = (
            f"The declaration from {result.supplier or 'the supplier'} was reviewed "
            f"against EU RoHS restrictions. All addressed substances are within limits "
            f"and no issues were identified."
        )
        out = AgentOutput(
            supplier=result.supplier,
            component=result.component,
            rating_label=result.label,
            explanation=explanation,
            email=NO_EMAIL,
            from_cache=False,
            attempts=0,
        )
        cache.put(sha, AGENT_OUTPUT_VERSION, MODEL,
                  {"explanation": out.explanation, "email": out.email, "attempts": 0},
                  cache_dir=effective_cache_dir)
        return out

    if trace_fn:
        trace_fn("Drafting follow-up")

    client = client or make_client()
    user_msg = _build_user_message(result)
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    # First attempt
    raw = _call_once(client, messages)
    try:
        explanation, email = _parse_response(raw)
    except ValueError as exc:
        raise AgentError(f"Could not parse model response: {exc}") from exc

    errors = _guard(explanation, email, result, rules)
    attempts = 1

    if errors:
        # Retry once, feeding the errors back.
        attempts = 2
        retry_msg = (
            "Your response failed the following checks:\n- "
            + "\n- ".join(errors)
            + "\n\nPlease regenerate both the EXPLANATION and EMAIL, "
            "correcting these issues. Use only substances and values "
            "present in the original findings."
        )
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": retry_msg},
        ]
        raw2 = _call_once(client, messages)
        try:
            explanation, email = _parse_response(raw2)
        except ValueError as exc:
            raise AgentError(f"Could not parse retry response: {exc}") from exc

        errors = _guard(explanation, email, result, rules)

    error_str: str | None = None
    if errors:
        error_str = "Guard checks failed after retry: " + "; ".join(errors)
        explanation = ""
        email = ""

    record = {
        "explanation": explanation,
        "email": email,
        "attempts": attempts,
        "error": error_str,
    }
    cache.put(sha, AGENT_OUTPUT_VERSION, MODEL, record, cache_dir=effective_cache_dir)

    return AgentOutput(
        supplier=result.supplier,
        component=result.component,
        rating_label=result.label,
        explanation=explanation,
        email=email,
        from_cache=False,
        attempts=attempts,
        error=error_str,
    )
