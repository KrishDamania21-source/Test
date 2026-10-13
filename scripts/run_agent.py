"""Run the full pipeline on all 8 samples and print explanations and emails.

Run from the repo root: python scripts/run_agent.py

Uses cached extractions if available (extraction API calls cost nothing on a
re-run). Always calls the API for agent outputs unless those are cached too.

Trace events are printed as they happen so you can see progress.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.agent import AgentError, draft  # noqa: E402
from radar.documents import load_path  # noqa: E402
from radar.engine import screen  # noqa: E402
from radar.extract import ExtractionError, extract, make_client  # noqa: E402
from radar.rules import load_active_rules, load_active_synonyms  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DIVIDER = "=" * 72


def trace(event: str) -> None:
    print(f"  > {event}")


def main() -> int:
    rules = load_active_rules()
    synonyms = load_active_synonyms()

    try:
        client = make_client()
    except Exception as exc:
        print(f"Cannot create API client: {exc}")
        return 1

    failed = 0
    for path in sorted(SAMPLES.glob("0*")):
        print(f"\n{DIVIDER}")
        print(f"  {path.name}")
        print(DIVIDER)

        # --- Step 1: load document
        trace("Reading document")
        doc = load_path(path)

        # --- Step 2: extract
        trace("Extracting substances")
        try:
            ext = extract(doc, client=client)
        except ExtractionError as exc:
            print(f"  EXTRACTION FAILED: {exc}")
            failed += 1
            continue
        src = "cache" if ext.from_cache else f"API ({ext.attempts} attempt(s))"
        print(f"     [{src}]  supplier: {ext.data.get('supplier')}  "
              f"substances: {len(ext.data.get('substances', []))}")

        # --- Step 3: screen against rules
        trace("Checking against EU RoHS")
        result = screen(ext.data, ext.document_text, rules, synonyms)

        # --- Step 4: rate
        trace("Rating risk")
        print(f"     Rating: {result.label}")
        non_nd = [f for f in result.findings if f.type != "NOT_DECLARED"]
        nd = [f for f in result.findings if f.type == "NOT_DECLARED"]
        for f in non_nd:
            print(f"     [{f.type}] {f.substance}: {f.message[:80]}")
        if nd:
            print(f"     NOT_DECLARED: {', '.join(f.substance for f in nd)}")

        # --- Step 5: draft
        try:
            out = draft(result, rules, client=client, trace_fn=trace)
        except AgentError as exc:
            print(f"  AGENT FAILED: {exc}")
            failed += 1
            continue

        src2 = "cache" if out.from_cache else f"API ({out.attempts} attempt(s))"
        print(f"     [{src2}]")

        if out.error:
            print(f"\n  ERROR: {out.error}")
            failed += 1
            continue

        print(f"\n  EXPLANATION:\n{out.explanation}\n")
        if out.email == "No follow-up needed.":
            print("  EMAIL: No follow-up needed.")
        else:
            # Indent each line of the email for readability.
            email_lines = out.email.splitlines()
            print("  EMAIL:")
            for line in email_lines:
                print(f"    {line}")

    print(f"\n{DIVIDER}")
    if failed:
        print(f"FAILURES: {failed} sample(s) failed.")
        return 1
    print("All samples processed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
