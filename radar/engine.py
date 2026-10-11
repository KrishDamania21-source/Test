"""Step 4: deterministic rules engine. No API calls, no model judgment.

Input:  an extraction (what the model read), the document text, and the
        active rules and synonyms.
Output: findings, each tied to the exact rule row it was judged against,
        and a risk rating.

Order of checks for each extracted substance:
  1. Traceability. If its source text is not in the document, the model
     may have invented it. Only UNTRACEABLE_EXTRACTION is raised for it:
     its values never produce a pass, a NEAR_LIMIT or an OVER_LIMIT.
  2. Matching to restricted substances (CAS first, then name and aliases).
     Substances that match no rule are not restricted and are skipped.
     If the name and the CAS point to different restricted substances,
     IDENTITY_CONFLICT is raised and the entry is judged under both
     readings, each against its own rule; the rating takes the worse one.
  3. CAS present? Otherwise MISSING_CAS.
  4. Concentration: missing, vague, unit unusable, exemption claimed, or a
     number compared against the limit.
Then every restricted substance not addressed at all gets NOT_DECLARED, and
a declaration that addresses none of them gets NO_SUBSTANCES_ADDRESSED.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from radar.rules import Rule, SubstanceMatcher, SynonymSet
from radar.units import MISSING, NUMERIC, UNKNOWN_UNIT, VAGUE, normalize

OVER_LIMIT = "OVER_LIMIT"
NEAR_LIMIT = "NEAR_LIMIT"
MISSING_CAS = "MISSING_CAS"
MISSING_CONCENTRATION = "MISSING_CONCENTRATION"
UNVERIFIABLE = "UNVERIFIABLE"
EXEMPTION_CLAIMED = "EXEMPTION_CLAIMED"
UNTRACEABLE_EXTRACTION = "UNTRACEABLE_EXTRACTION"
NOT_DECLARED = "NOT_DECLARED"
NO_SUBSTANCES_ADDRESSED = "NO_SUBSTANCES_ADDRESSED"
IDENTITY_CONFLICT = "IDENTITY_CONFLICT"

HIGH, MEDIUM, LOW, INFO = "High", "Medium", "Low", "Info"

SEVERITY = {
    OVER_LIMIT: HIGH,
    UNTRACEABLE_EXTRACTION: MEDIUM,
    MISSING_CAS: MEDIUM,
    MISSING_CONCENTRATION: MEDIUM,
    UNVERIFIABLE: MEDIUM,
    EXEMPTION_CLAIMED: MEDIUM,
    NO_SUBSTANCES_ADDRESSED: MEDIUM,
    NEAR_LIMIT: LOW,
    NOT_DECLARED: INFO,
    # Pending Krish: whether a conflict whose readings are both clean forces Medium.
    # Until he decides it does not, so it is Info and the readings carry the rating.
    IDENTITY_CONFLICT: INFO,
}
_SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3}
NEEDS_REVIEW_TYPES = {MISSING_CAS, MISSING_CONCENTRATION, UNVERIFIABLE, EXEMPTION_CLAIMED, UNTRACEABLE_EXTRACTION,
                      NO_SUBSTANCES_ADDRESSED}

NAME_READING, CAS_READING = "name", "cas"

NEAR_LIMIT_FRACTION = 0.8
_EPS = 1e-9  # relative tolerance for float comparisons at the boundaries


@dataclass(frozen=True)
class Finding:
    type: str
    severity: str
    substance: str                   # rule substance name, or the extracted name if unmatched
    message: str                     # deterministic, from a template
    rule: Rule | None = None         # the exact rule row this was judged against
    extracted_name: str | None = None
    cas: str | None = None
    value_pct: float | None = None   # concentration in % after Python unit conversion
    raw_value: str | None = None     # exactly as written in the document
    source_text: str | None = None
    reading: str | None = None       # "name" or "cas" when the entry has an identity conflict

    @property
    def rule_id(self) -> str | None:
        return self.rule.rule_id if self.rule else None

    @property
    def limit_pct(self) -> float | None:
        return self.rule.limit_pct if self.rule else None


@dataclass
class ScreeningResult:
    supplier: str | None
    component: str | None
    findings: list[Finding]
    rating: str                      # High, Medium or Low
    needs_review: bool
    watch: bool
    rule_versions: dict = field(default_factory=dict)  # which pack and synonym versions were used

    @property
    def label(self) -> str:
        if self.rating == HIGH:
            return "High"
        if self.rating == MEDIUM:
            return "Medium / Needs review"
        return "Low / Watch" if self.watch else "Low"

    def of_type(self, finding_type: str) -> list[Finding]:
        return [f for f in self.findings if f.type == finding_type]


# ---------------------------------------------------------------- helpers

def _squash(text: str) -> str:
    return " ".join(text.lower().split())


def is_traceable(source_text: str | None, document_text: str) -> bool:
    """True if the source text appears in the document, ignoring case and whitespace."""
    if not source_text or not source_text.strip():
        return False
    return _squash(source_text) in _squash(document_text)


def _fmt(pct: float) -> str:
    return f"{pct:.6f}".rstrip("0").rstrip(".") + "%"


def _over(value: float, limit: float) -> bool:
    return value > limit * (1 + _EPS)


def _at_or_above_near(value: float, limit: float) -> bool:
    return value >= limit * NEAR_LIMIT_FRACTION * (1 - _EPS)


def rate(findings: list[Finding]) -> tuple[str, bool, bool]:
    """Risk rating from findings. NOT_DECLARED (Info) never changes it."""
    types = {f.type for f in findings}
    if OVER_LIMIT in types:
        return HIGH, bool(types & NEEDS_REVIEW_TYPES), NEAR_LIMIT in types
    if types & NEEDS_REVIEW_TYPES:
        return MEDIUM, True, NEAR_LIMIT in types
    if NEAR_LIMIT in types:
        return LOW, False, True
    return LOW, False, False


# ---------------------------------------------------------------- per substance

def _concentration_findings(item: dict, rule: Rule, base: dict) -> list[Finding]:
    conc = normalize(item.get("concentration_raw"), item.get("concentration_value"), item.get("unit"))
    name = rule.substance
    limit = rule.limit_pct
    raw = conc.raw

    if item.get("exemption_claimed"):
        # Checked first: an exemption claim always routes to a person, whatever the value says.
        if conc.status == NUMERIC:
            relation = "above" if _over(conc.percent, limit) else "within"
            detail = f"{_fmt(conc.percent)} is {relation} the base limit of {_fmt(limit)}"
        else:
            detail = f"value \"{raw or 'not given'}\" against a base limit of {_fmt(limit)}"
        return [Finding(EXEMPTION_CLAIMED, SEVERITY[EXEMPTION_CLAIMED], name,
                        f"{name}: {detail}. Exemption claimed: \"{item.get('exemption_text')}\". "
                        f"Exemptions are never auto-accepted; a person must confirm it applies.",
                        **{**base, "value_pct": conc.percent})]

    if conc.status == MISSING:
        return [Finding(MISSING_CONCENTRATION, SEVERITY[MISSING_CONCENTRATION], name,
                        f"{name}: no concentration given. Limit is {_fmt(limit)}.", **base)]
    if conc.status == VAGUE:
        return [Finding(UNVERIFIABLE, SEVERITY[UNVERIFIABLE], name,
                        f"{name}: value \"{raw}\" cannot be compared to the {_fmt(limit)} limit.", **base)]
    if conc.status == UNKNOWN_UNIT:
        return [Finding(UNVERIFIABLE, SEVERITY[UNVERIFIABLE], name,
                        f"{name}: value \"{raw}\" has no usable unit, so it cannot be compared to the "
                        f"{_fmt(limit)} limit.", **base)]

    assert conc.status == NUMERIC
    value = conc.percent
    base = {**base, "value_pct": value}

    q = conc.qualifier
    if q in ("<", "<="):
        # The true value is at most `value`.
        if _over(value, limit):
            return [Finding(UNVERIFIABLE, SEVERITY[UNVERIFIABLE], name,
                            f"{name}: declared as \"{raw}\". The upper bound {_fmt(value)} is above the "
                            f"{_fmt(limit)} limit, so compliance cannot be confirmed.", **base)]
    elif q in (">", ">="):
        # The true value is at least `value`.
        definitely_over = _over(value, limit) or (q == ">" and value >= limit * (1 - _EPS))
        if definitely_over:
            return [Finding(OVER_LIMIT, SEVERITY[OVER_LIMIT], name,
                            f"{name}: declared as \"{raw}\", above the {_fmt(limit)} limit.", **base)]
        return [Finding(UNVERIFIABLE, SEVERITY[UNVERIFIABLE], name,
                        f"{name}: declared as \"{raw}\", a lower bound only. It cannot be confirmed to be "
                        f"within the {_fmt(limit)} limit.", **base)]
    elif _over(value, limit):
        return [Finding(OVER_LIMIT, SEVERITY[OVER_LIMIT], name,
                        f"{name}: {_fmt(value)} is above the {_fmt(limit)} limit "
                        f"({value / limit:.1f}x the limit).", **base)]

    if _at_or_above_near(value, limit):
        return [Finding(NEAR_LIMIT, SEVERITY[NEAR_LIMIT], name,
                        f"{name}: {_fmt(value)} is within 80% of the {_fmt(limit)} limit "
                        f"({value / limit:.0%} of the limit).", **base)]
    return []


def screen(extraction: dict, document_text: str, rules: list[Rule], synonyms: SynonymSet) -> ScreeningResult:
    restrictions = [r for r in rules if r.obligation_type == "restriction"]
    matcher = SubstanceMatcher(restrictions, synonyms)
    findings: list[Finding] = []
    addressed: set[str] = set()  # rule_ids touched by any extracted substance

    for item in extraction.get("substances", []):
        name = item.get("name") or "(unnamed)"
        cas = (item.get("cas") or "").strip() or None
        source = item.get("source_text")
        base = {"extracted_name": name, "cas": cas, "raw_value": item.get("concentration_raw"),
                "source_text": source}

        # Each reading is (label or None, rules). Normally one reading; two on a conflict.
        by_name, by_cas = matcher.match_name(name), matcher.match_cas(cas)
        if by_name and by_cas and set(by_name) != set(by_cas):
            readings = [(NAME_READING, by_name), (CAS_READING, [r for r in by_cas if r not in by_name])]
            name_subs = ", ".join(r.substance for r in by_name)
            cas_subs = ", ".join(r.substance for r in by_cas)
            findings.append(Finding(
                IDENTITY_CONFLICT, SEVERITY[IDENTITY_CONFLICT], f"{name_subs} / {cas_subs}",
                f"\"{name}\" names {name_subs}, but CAS {cas} belongs to {cas_subs}. The entry is judged "
                f"under both readings and rated by the worse one. A person must confirm which is meant.",
                **base))
        else:
            readings = [(None, by_cas or by_name)]

        for reading, matches in readings:
            addressed.update(r.rule_id for r in matches)
            tag = {"reading": reading}
            prefix = {NAME_READING: "Reading by name: ", CAS_READING: "Reading by CAS: ", None: ""}[reading]

            if not is_traceable(source, document_text):
                # The model may have invented this. Nothing about it is trusted: no pass, no limit check.
                for rule in matches or [None]:
                    label = rule.substance if rule else name
                    findings.append(Finding(
                        UNTRACEABLE_EXTRACTION, SEVERITY[UNTRACEABLE_EXTRACTION], label,
                        f"{prefix}{label}: the extracted source text was not found in the document, so this "
                        f"entry cannot be traced to the supplier's words. A person must check the original.",
                        rule=rule, **base, **tag))
                continue

            for rule in matches:
                if not cas:
                    findings.append(Finding(MISSING_CAS, SEVERITY[MISSING_CAS], rule.substance,
                                            f"{rule.substance}: no CAS number given.", rule=rule, **base))
                for f in _concentration_findings(item, rule, {**base, "rule": rule}):
                    findings.append(replace(f, message=prefix + f.message, **tag))

    for rule in restrictions:
        if rule.rule_id not in addressed:
            findings.append(Finding(NOT_DECLARED, SEVERITY[NOT_DECLARED], rule.substance,
                                    f"{rule.substance}: not addressed in the declaration. Please confirm.",
                                    rule=rule))
    if restrictions and not addressed:
        findings.append(Finding(NO_SUBSTANCES_ADDRESSED, SEVERITY[NO_SUBSTANCES_ADDRESSED], "(all)",
                                "The declaration does not address any of the restricted substances, so "
                                "nothing in it can be checked. A person must review it."))

    findings.sort(key=lambda f: _SEVERITY_ORDER[f.severity])
    rating, needs_review, watch = rate(findings)
    versions = {r.pack_id: r.version_id for r in restrictions}
    versions["synonyms"] = synonyms.version_id
    return ScreeningResult(
        supplier=extraction.get("supplier"), component=extraction.get("component"),
        findings=findings, rating=rating, needs_review=needs_review, watch=watch, rule_versions=versions,
    )
