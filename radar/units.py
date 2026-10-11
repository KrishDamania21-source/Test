"""Deterministic concentration handling. The model never converts units.

The extractor returns the value exactly as written (concentration_raw), the
plain number it contains (concentration_value) and the unit label. This
module turns that into a % by weight figure the rules engine can compare,
or says why it cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Factor that turns one unit into % by weight. 1 % = 10,000 ppm = 10,000 mg/kg.
TO_PERCENT = {
    "%": 1.0,
    "ppm": 1 / 10_000,
    "mg/kg": 1 / 10_000,
}

# Status of a concentration once normalized.
NUMERIC = "numeric"            # comparable number in %
VAGUE = "vague"                # text present but no number (trace amounts, N/A, <LOD)
MISSING = "missing"            # nothing given at all
UNKNOWN_UNIT = "unknown_unit"  # a number with no usable unit

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
_QUALIFIER_RE = re.compile(r"^\s*(<=|>=|≤|≥|<|>|~|approx\.?|ca\.?)", re.IGNORECASE)
_QUALIFIER_MAP = {"≤": "<=", "≥": ">=", "~": "~", "approx": "~", "approx.": "~", "ca": "~", "ca.": "~"}


@dataclass(frozen=True)
class Concentration:
    status: str
    percent: float | None = None   # % by weight, only when status is NUMERIC
    qualifier: str = "="           # "=", "<", "<=", ">", ">=", "~"
    raw: str | None = None
    unit: str | None = None

    @property
    def ppm(self) -> float | None:
        return None if self.percent is None else self.percent * 10_000


def numbers_in(text: str | None) -> list[float]:
    """Every number written in a piece of text, e.g. '<0.001 %' -> [0.001]."""
    if not text:
        return []
    return [float(m.group().replace(",", ".")) for m in _NUMBER_RE.finditer(text)]


def qualifier_of(raw: str | None) -> str:
    if not raw:
        return "="
    m = _QUALIFIER_RE.match(raw)
    if not m:
        return "="
    q = m.group(1).lower()
    return _QUALIFIER_MAP.get(q, q)


def normalize(raw: str | None, value: float | None, unit: str | None) -> Concentration:
    raw_clean = (raw or "").strip() or None
    if value is None:
        status = VAGUE if raw_clean else MISSING
        return Concentration(status=status, raw=raw_clean, unit=unit)
    factor = TO_PERCENT.get(unit or "")
    if factor is None:
        return Concentration(status=UNKNOWN_UNIT, raw=raw_clean, unit=unit)
    return Concentration(
        status=NUMERIC,
        percent=value * factor,
        qualifier=qualifier_of(raw_clean),
        raw=raw_clean,
        unit=unit,
    )
