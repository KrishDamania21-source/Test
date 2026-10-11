"""Compare rules-engine output on cached extractions against answer_key.json.

Run from the repo root: python scripts/eval_live.py
No API calls needed: reads from .cache/extractions/.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import radar.cache as cache  # noqa: E402
from radar.engine import screen  # noqa: E402
from radar.extract import EXTRACTION_VERSION, MODEL  # noqa: E402
from radar.rules import load_active_rules, load_active_synonyms  # noqa: E402


def load_cached(path: Path):
    """Return the cached extraction record for a sample file, or None."""
    data = path.read_bytes()
    sha = cache.file_hash(data)
    return cache.get(sha, EXTRACTION_VERSION, MODEL)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
answer_key = json.loads((SAMPLES / "answer_key.json").read_text())["samples"]

rules = load_active_rules()
synonyms = load_active_synonyms()

passed = 0
total = 0

for sample_name, expected in answer_key.items():
    total += 1
    sample_path = SAMPLES / sample_name
    cached = load_cached(sample_path)
    if cached is None:
        print(f"\nNO CACHE: {sample_name}  -- run scripts/extract_samples.py first")
        continue

    result = screen(cached["data"], cached["document_text"], rules, synonyms)

    exp_risk = expected["expected_risk"]
    exp_watch = expected["watch_flag"]
    rating_ok = result.rating == exp_risk
    watch_ok = result.watch == exp_watch

    got_typed = sorted((f.type, f.substance) for f in result.findings if f.type != "NOT_DECLARED")
    exp_typed = sorted((f["type"], f["substance"]) for f in expected["expected_findings"] if f["type"] != "NOT_DECLARED")
    got_nd = {f.substance for f in result.findings if f.type == "NOT_DECLARED"}
    exp_nd = {f["substance"] for f in expected["expected_findings"] if f["type"] == "NOT_DECLARED"}

    findings_ok = got_typed == exp_typed
    nd_ok = got_nd == exp_nd
    ok = rating_ok and watch_ok and findings_ok and nd_ok

    if ok:
        passed += 1

    print(f"\n{'='*72}")
    print(f"{'PASS' if ok else 'FAIL'}  {sample_name}")
    print(f"  Rating:  got={result.rating} watch={result.watch}  "
          f"expected={exp_risk} watch={exp_watch}  "
          f"{'OK' if (rating_ok and watch_ok) else 'MISMATCH'}")

    if findings_ok:
        print(f"  Non-ND findings: OK  {got_typed}")
    else:
        print("  Non-ND findings: MISMATCH")
        got_set, exp_set = set(got_typed), set(exp_typed)
        for x in sorted(exp_set - got_set):
            print(f"    MISSING: {x}")
        for x in sorted(got_set - exp_set):
            print(f"    EXTRA:   {x}")

    if nd_ok:
        print(f"  NOT_DECLARED:    OK  ({len(got_nd)} substances)")
    else:
        print("  NOT_DECLARED:    MISMATCH")
        for x in sorted(exp_nd - got_nd):
            print(f"    MISSING: {x}")
        for x in sorted(got_nd - exp_nd):
            print(f"    EXTRA:   {x}")

print(f"\n{'='*72}")
print(f"OVERALL: {passed}/{total} samples pass")
sys.exit(0 if passed == total else 1)
