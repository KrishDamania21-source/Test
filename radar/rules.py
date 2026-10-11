"""Rule packs: strict loading, validation, versioning and substance matching.

Rules live as data in rules/, never as Python constants:

    rules/packs/<pack_id>/<version_id>.csv   one full pack per version file
    rules/synonyms/<version_id>.json         matching vocabulary, versioned the same way
    rules/active.json                        {"packs": {pack_id: version_id}, "synonyms": version_id}
    rules/history.json                       approvals, rollbacks, seeding

Synonyms hold no limits, but a wrong alias can cause a missed or wrong
match, so they go through the same validation, sign-off and approval as
rule packs.

The engine, the Rules tab and every citation read rules only through
load_active_rules(). Rules come only from files a human provides and
approves; the model never generates or edits them.

Validation is all or nothing: any bad row rejects the whole file with
row-level errors, and nothing is partially loaded.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

COLUMNS = (
    "pack_id",
    "regulation_name",
    "short_code",
    "jurisdiction",
    "obligation_type",
    "substance",
    "cas_numbers",
    "limit_pct",
    "legal_reference",
    "source_url",
    "version_date",
    "last_verified",
    "verified_by",
    "is_subset",
)
OBLIGATION_TYPES = ("restriction", "disclosure", "warning")
# Fields that describe the regulation, so they must be identical on every row of a pack.
PACK_LEVEL_FIELDS = ("pack_id", "regulation_name", "short_code", "jurisdiction")

_PACK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_CAS_RE = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")
_VERSION_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")


class RulePackError(ValueError):
    """Raised when a rule pack file fails validation. Carries every row-level error."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Rule pack rejected with {len(errors)} error(s):\n" + "\n".join(errors))


@dataclass(frozen=True)
class Rule:
    pack_id: str
    regulation_name: str
    short_code: str
    jurisdiction: str
    obligation_type: str
    substance: str
    cas_numbers: tuple[str, ...]
    limit_pct: float | None
    legal_reference: str
    source_url: str
    version_date: date
    last_verified: date
    verified_by: str
    is_subset: bool
    version_id: str = ""

    @property
    def rule_id(self) -> str:
        """Stable citation key: pack, version and substance."""
        return f"{self.pack_id}@{self.version_id}#{slugify(self.substance)}"

    @property
    def limit_ppm(self) -> float | None:
        return None if self.limit_pct is None else self.limit_pct * 10_000


@dataclass(frozen=True)
class RulePack:
    pack_id: str
    version_id: str
    rules: tuple[Rule, ...]

    @property
    def regulation_name(self) -> str:
        return self.rules[0].regulation_name

    @property
    def short_code(self) -> str:
        return self.rules[0].short_code

    @property
    def is_subset(self) -> bool:
        return any(r.is_subset for r in self.rules)


# ---------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def normalize_name(text: str) -> str:
    """Lowercase, treat punctuation as spaces, collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def normalize_cas(text: str | None) -> str | None:
    """Pull a CAS number out of free text, e.g. 'CAS 7439-92-1' -> '7439-92-1'."""
    if not text:
        return None
    m = re.search(r"(\d{2,7})\s*-\s*(\d{2})\s*-\s*(\d)", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def cas_checksum_ok(cas: str) -> bool:
    """CAS check digit: weighted sum of the other digits, right to left, mod 10."""
    m = _CAS_RE.match(cas)
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    return sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10 == int(m.group(3))


def _parse_date(value: str, field: str, errors: list[str], where: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"{where}: {field} '{value}' is not an ISO date (YYYY-MM-DD)")
        return None


# ---------------------------------------------------------------- parsing

def parse_csv_text(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = tuple(h.strip() for h in (reader.fieldnames or ()))
    missing = [c for c in COLUMNS if c not in header]
    extra = [c for c in header if c not in COLUMNS]
    if missing or extra:
        errors = []
        if missing:
            errors.append(f"header: missing column(s): {', '.join(missing)}")
        if extra:
            errors.append(f"header: unknown column(s): {', '.join(extra)}")
        raise RulePackError(errors)
    rows = []
    for raw in reader:
        if None in raw:
            raise RulePackError([f"row {reader.line_num}: more values than columns"])
        rows.append({k.strip(): (v or "").strip() for k, v in raw.items()})
    return rows


def parse_json_text(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RulePackError([f"file: invalid JSON ({e})"]) from e
    if isinstance(data, dict) and "rules" in data:
        data = data["rules"]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise RulePackError(["file: JSON must be a list of rule objects, or {\"rules\": [...]}"])
    rows, errors = [], []
    for i, raw in enumerate(data, start=1):
        keys = set(raw)
        if missing := [c for c in COLUMNS if c not in keys]:
            errors.append(f"row {i}: missing field(s): {', '.join(missing)}")
        if extra := sorted(keys - set(COLUMNS)):
            errors.append(f"row {i}: unknown field(s): {', '.join(extra)}")
        row = {}
        for k in COLUMNS:
            v = raw.get(k)
            if isinstance(v, list):
                v = ";".join(str(x) for x in v)
            elif isinstance(v, bool):
                v = "true" if v else "false"
            row[k] = "" if v is None else str(v).strip()
        rows.append(row)
    if errors:
        raise RulePackError(errors)
    return rows


def validate_rows(rows: list[dict], version_id: str = "") -> RulePack:
    """Validate every row and return a RulePack, or raise RulePackError listing all problems."""
    errors: list[str] = []
    if not rows:
        raise RulePackError(["file: contains no rule rows"])

    rules: list[Rule] = []
    seen_substances: dict[str, int] = {}
    seen_cas: dict[str, int] = {}
    first = rows[0]

    for i, row in enumerate(rows, start=1):
        where = f"row {i}"
        n_before = len(errors)

        for field in ("pack_id", "regulation_name", "short_code", "jurisdiction", "substance",
                      "legal_reference", "source_url", "verified_by"):
            if not row[field]:
                errors.append(f"{where}: {field} is required")

        if row["pack_id"] and not _PACK_ID_RE.match(row["pack_id"]):
            errors.append(f"{where}: pack_id '{row['pack_id']}' must be lowercase letters, digits, underscores")
        for field in PACK_LEVEL_FIELDS:
            if row[field] != first[field]:
                errors.append(f"{where}: {field} '{row[field]}' differs from row 1 ('{first[field]}'); one pack per file")

        ob = row["obligation_type"]
        if ob not in OBLIGATION_TYPES:
            errors.append(f"{where}: obligation_type '{ob}' must be one of {', '.join(OBLIGATION_TYPES)}")

        limit: float | None = None
        if row["limit_pct"]:
            try:
                limit = float(row["limit_pct"])
                if not 0 < limit <= 100:
                    errors.append(f"{where}: limit_pct {limit} must be > 0 and <= 100")
            except ValueError:
                errors.append(f"{where}: limit_pct '{row['limit_pct']}' is not a number")
        if ob in ("restriction", "disclosure") and not row["limit_pct"]:
            errors.append(f"{where}: limit_pct is required for obligation_type '{ob}'")
        if ob == "warning" and row["limit_pct"]:
            errors.append(f"{where}: limit_pct must be empty for obligation_type 'warning'")

        cas_list = tuple(c.strip() for c in row["cas_numbers"].split(";") if c.strip())
        for cas in cas_list:
            if not _CAS_RE.match(cas):
                errors.append(f"{where}: CAS '{cas}' is not in NNNNNNN-NN-N format")
            elif not cas_checksum_ok(cas):
                errors.append(f"{where}: CAS '{cas}' fails its check digit")
            elif cas in seen_cas:
                errors.append(f"{where}: CAS '{cas}' already used on row {seen_cas[cas]}")
            else:
                seen_cas[cas] = i

        key = normalize_name(row["substance"])
        if key and key in seen_substances:
            errors.append(f"{where}: substance '{row['substance']}' duplicates row {seen_substances[key]}")
        elif key:
            seen_substances[key] = i

        if row["source_url"] and not row["source_url"].startswith("https://"):
            errors.append(f"{where}: source_url must start with https://")

        vdate = _parse_date(row["version_date"], "version_date", errors, where)
        ldate = _parse_date(row["last_verified"], "last_verified", errors, where)
        if vdate and ldate and ldate < vdate:
            errors.append(f"{where}: last_verified {ldate} is before version_date {vdate}")

        if row["is_subset"].lower() not in ("true", "false"):
            errors.append(f"{where}: is_subset '{row['is_subset']}' must be true or false")

        if len(errors) == n_before:
            rules.append(Rule(
                pack_id=row["pack_id"],
                regulation_name=row["regulation_name"],
                short_code=row["short_code"],
                jurisdiction=row["jurisdiction"],
                obligation_type=ob,
                substance=row["substance"],
                cas_numbers=cas_list,
                limit_pct=limit,
                legal_reference=row["legal_reference"],
                source_url=row["source_url"],
                version_date=vdate,
                last_verified=ldate,
                verified_by=row["verified_by"],
                is_subset=row["is_subset"].lower() == "true",
                version_id=version_id,
            ))

    if errors:
        raise RulePackError(errors)
    return RulePack(pack_id=first["pack_id"], version_id=version_id, rules=tuple(rules))


def parse_pack_text(text: str, filename: str, version_id: str = "") -> RulePack:
    """Parse and validate an uploaded or stored pack. Format is chosen by extension."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        rows = parse_csv_text(text)
    elif suffix == ".json":
        rows = parse_json_text(text)
    else:
        raise RulePackError([f"file: unsupported type '{suffix}', use .csv or .json"])
    return validate_rows(rows, version_id=version_id)


# ---------------------------------------------------------------- storage

def load_pack_version(pack_id: str, version_id: str, rules_dir: Path = RULES_DIR) -> RulePack:
    if not _VERSION_ID_RE.match(version_id):
        raise RulePackError([f"active.json: version id '{version_id}' is not YYYYMMDDTHHMMSSZ"])
    path = rules_dir / "packs" / pack_id / f"{version_id}.csv"
    if not path.exists():
        raise RulePackError([f"active.json: {path.relative_to(rules_dir)} does not exist"])
    pack = parse_pack_text(path.read_text(encoding="utf-8"), path.name, version_id=version_id)
    if pack.pack_id != pack_id:
        raise RulePackError([f"{path.name}: pack_id '{pack.pack_id}' does not match folder '{pack_id}'"])
    return pack


def list_versions(pack_id: str, rules_dir: Path = RULES_DIR) -> list[str]:
    folder = rules_dir / "packs" / pack_id
    return sorted(p.stem for p in folder.glob("*.csv") if _VERSION_ID_RE.match(p.stem))


def read_active(rules_dir: Path = RULES_DIR) -> dict:
    data = json.loads((rules_dir / "active.json").read_text(encoding="utf-8"))
    if set(data) != {"packs", "synonyms"} or not isinstance(data["packs"], dict):
        raise RulePackError(['active.json: must be {"packs": {...}, "synonyms": "<version_id>"}'])
    return data


def active_versions(rules_dir: Path = RULES_DIR) -> dict[str, str]:
    return read_active(rules_dir)["packs"]


def load_active_packs(rules_dir: Path = RULES_DIR) -> list[RulePack]:
    """The only way the rest of RADAR gets rules: the active version of each pack."""
    return [load_pack_version(pid, vid, rules_dir) for pid, vid in sorted(active_versions(rules_dir).items())]


def load_active_rules(rules_dir: Path = RULES_DIR) -> list[Rule]:
    return [r for pack in load_active_packs(rules_dir) for r in pack.rules]


# ---------------------------------------------------------------- matching

@dataclass(frozen=True)
class SynonymSet:
    version_id: str
    last_verified: date
    verified_by: str
    synonyms: dict[str, tuple[str, ...]]  # substance name as in packs -> aliases

    def aliases_for(self, substance: str) -> tuple[str, ...]:
        key = normalize_name(substance)
        for name, aliases in self.synonyms.items():
            if normalize_name(name) == key:
                return aliases
        return ()


SYNONYM_FIELDS = {"about", "last_verified", "verified_by", "synonyms"}


def parse_synonyms_text(text: str, version_id: str = "") -> SynonymSet:
    """Validate a synonyms file. All or nothing, like rule packs.

    Rejects an alias that points at two different substances, because the
    matcher could not tell which one a supplier meant.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RulePackError([f"file: invalid JSON ({e})"]) from e
    if not isinstance(data, dict):
        raise RulePackError(["file: synonyms must be a JSON object"])

    errors: list[str] = []
    if missing := sorted({"last_verified", "verified_by", "synonyms"} - set(data)):
        errors.append(f"file: missing field(s): {', '.join(missing)}")
    if extra := sorted(set(data) - SYNONYM_FIELDS):
        errors.append(f"file: unknown field(s): {', '.join(extra)}")
    if errors:
        raise RulePackError(errors)

    verified = _parse_date(str(data["last_verified"]), "last_verified", errors, "file")
    if not str(data["verified_by"]).strip():
        errors.append("file: verified_by is required")
    if not isinstance(data["synonyms"], dict) or not data["synonyms"]:
        raise RulePackError(errors + ["file: synonyms must be a non-empty object"])

    owner: dict[str, str] = {}  # normalized name or alias -> substance that claims it
    clean: dict[str, tuple[str, ...]] = {}
    for name in data["synonyms"]:
        if normalize_name(name):
            owner.setdefault(normalize_name(name), name)

    for name, aliases in data["synonyms"].items():
        where = f"substance '{name}'"
        if not normalize_name(name):
            errors.append(f"{where}: substance name is empty")
            continue
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            errors.append(f"{where}: aliases must be a list of strings")
            continue
        for alias in aliases:
            key = normalize_name(alias)
            if not key:
                errors.append(f"{where}: alias '{alias}' is empty after normalization")
            elif owner.get(key, name) != name:
                errors.append(f"{where}: alias '{alias}' is already used by '{owner[key]}'")
            else:
                owner[key] = name
        clean[name] = tuple(aliases)

    if errors:
        raise RulePackError(errors)
    return SynonymSet(version_id=version_id, last_verified=verified,
                      verified_by=str(data["verified_by"]).strip(), synonyms=clean)


def list_synonym_versions(rules_dir: Path = RULES_DIR) -> list[str]:
    return sorted(p.stem for p in (rules_dir / "synonyms").glob("*.json") if _VERSION_ID_RE.match(p.stem))


def load_synonym_version(version_id: str, rules_dir: Path = RULES_DIR) -> SynonymSet:
    if not _VERSION_ID_RE.match(version_id):
        raise RulePackError([f"active.json: synonyms version '{version_id}' is not YYYYMMDDTHHMMSSZ"])
    path = rules_dir / "synonyms" / f"{version_id}.json"
    if not path.exists():
        raise RulePackError([f"active.json: {path.relative_to(rules_dir)} does not exist"])
    return parse_synonyms_text(path.read_text(encoding="utf-8"), version_id=version_id)


def load_active_synonyms(rules_dir: Path = RULES_DIR) -> SynonymSet:
    return load_synonym_version(read_active(rules_dir)["synonyms"], rules_dir)


class SubstanceMatcher:
    """Maps a declared substance (name and/or CAS) to rule rows.

    CAS wins when it is a number a rule lists. Otherwise the name is tried
    in full, then without its parenthetical, then the parenthetical alone,
    so 'Lead (Pb)', 'Pb' and 'lead' all match the Lead row.
    """

    def __init__(self, rules: list[Rule], synonyms: SynonymSet):
        self.rules = rules
        self._by_cas: dict[str, list[Rule]] = {}
        self._by_name: dict[str, list[Rule]] = {}
        for rule in rules:
            for cas in rule.cas_numbers:
                self._by_cas.setdefault(cas, []).append(rule)
            names = {rule.substance, *synonyms.aliases_for(rule.substance)}
            for name in names:
                bucket = self._by_name.setdefault(normalize_name(name), [])
                if rule not in bucket:
                    bucket.append(rule)

    def match(self, name: str | None, cas: str | None) -> list[Rule]:
        return self.match_cas(cas) or self.match_name(name)

    def match_cas(self, cas: str | None) -> list[Rule]:
        norm = normalize_cas(cas)
        return list(self._by_cas.get(norm, [])) if norm else []

    def match_name(self, name: str | None) -> list[Rule]:
        if not name:
            return []
        candidates = [name, re.sub(r"\(.*?\)", " ", name), *re.findall(r"\((.*?)\)", name)]
        for candidate in candidates:
            hit = self._by_name.get(normalize_name(candidate))
            if hit:
                return list(hit)
        return []
