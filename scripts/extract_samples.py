"""Run extraction on every sample and print what the model read.

Run from the repo root: python scripts/extract_samples.py
Needs ANTHROPIC_API_KEY in the environment. Cached documents cost nothing.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.documents import load_path  # noqa: E402
from radar.extract import ExtractionError, extract  # noqa: E402
from radar.units import normalize  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in the environment. Nothing was sent.")
        return 1
    total_in = total_out = calls = 0
    failed = False
    for path in sorted(SAMPLES.glob("0*")):
        try:
            result = extract(load_path(path))
        except ExtractionError as e:
            print(f"\n{path.name}: FAILED: {e}")
            failed = True
            continue
        d = result.data
        source = "cache" if result.from_cache else f"API, {result.attempts} attempt(s)"
        print(f"\n{path.name}  [{source}]")
        print(f"  supplier: {d['supplier']}  |  component: {d['component']}")
        for s in d["substances"]:
            c = normalize(s["concentration_raw"], s["concentration_value"], s["unit"])
            pct = f"{c.percent:.4g} %" if c.percent is not None else c.status
            flag = "  EXEMPTION CLAIMED" if s["exemption_claimed"] else ""
            print(f"  - {s['name']:<28} CAS {s['cas'] or '-':<12} raw {s['concentration_raw']!r:<18} -> {pct}{flag}")
        for w in result.warnings:
            print(f"  warning: {w}")
        if not result.from_cache:
            calls += result.attempts
            total_in += result.usage.get("input_tokens", 0)
            total_out += result.usage.get("output_tokens", 0)
    print(f"\nAPI calls this run: {calls}  (input tokens {total_in}, output tokens {total_out})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
