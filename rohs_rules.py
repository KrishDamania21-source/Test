"""EU RoHS restricted substances and their maximum concentration values.

Source:
    Directive 2011/65/EU of the European Parliament and of the Council
    (RoHS 2), Annex II "Restricted substances referred to in Article 4(1)
    and maximum concentration values tolerated by weight in homogeneous
    materials", as amended by Commission Delegated Directive (EU) 2015/863,
    which added the four phthalates (DEHP, BBP, DBP, DIBP).
    Consolidated text: https://eur-lex.europa.eu/eli/dir/2011/65/oj

    Annex II limits: 0.1 % by weight in homogeneous material for every
    substance except cadmium, which is 0.01 %.

    The phthalate restriction applies from 22 July 2019 (22 July 2021 for
    medical devices and monitoring and control instruments). This prototype
    applies all 10 restrictions unconditionally.

CAS numbers are not part of Annex II itself; they are added here to help
match supplier declarations. PBB and PBDE are substance groups, so they are
matched by group name and by the CAS numbers of their common members or
commercial mixtures. The CAS lists for those groups are not exhaustive.

This module is pure data plus lookup helpers. It makes no API calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PERCENT_TO_PPM = 10_000  # 1 % by weight = 10,000 ppm (mg/kg)


@dataclass(frozen=True)
class RestrictedSubstance:
    key: str                      # stable internal identifier
    name: str                     # display name
    symbol: str                   # short form used in reports
    cas_numbers: tuple[str, ...]  # known CAS numbers (groups: common members)
    max_percent: float            # max concentration, % by weight
    is_group: bool = False        # True for PBB and PBDE
    aliases: tuple[str, ...] = () # extra names accepted when matching

    @property
    def max_ppm(self) -> float:
        return self.max_percent * PERCENT_TO_PPM


RESTRICTED_SUBSTANCES: tuple[RestrictedSubstance, ...] = (
    RestrictedSubstance(
        key="lead",
        name="Lead",
        symbol="Pb",
        cas_numbers=("7439-92-1",),
        max_percent=0.1,
        aliases=("lead", "pb", "lead pb"),
    ),
    RestrictedSubstance(
        key="mercury",
        name="Mercury",
        symbol="Hg",
        cas_numbers=("7439-97-6",),
        max_percent=0.1,
        aliases=("mercury", "hg", "mercury hg"),
    ),
    RestrictedSubstance(
        key="cadmium",
        name="Cadmium",
        symbol="Cd",
        cas_numbers=("7440-43-9",),
        max_percent=0.01,
        aliases=("cadmium", "cd", "cadmium cd"),
    ),
    RestrictedSubstance(
        key="chromium_vi",
        name="Hexavalent chromium",
        symbol="Cr(VI)",
        cas_numbers=("18540-29-9",),
        max_percent=0.1,
        aliases=(
            "hexavalent chromium",
            "chromium vi",
            "chromium 6",
            "chromium hexavalent",
            "crvi",
            "cr vi",
            "cr6",
            "cr 6",
            "hexavalent chromium crvi",
            "hexavalent chromium cr vi",
        ),
    ),
    RestrictedSubstance(
        key="pbb",
        name="Polybrominated biphenyls",
        symbol="PBB",
        cas_numbers=(
            "59536-65-1",  # polybrominated biphenyls (generic)
            "36355-01-8",  # hexabromobiphenyl
            "27858-07-7",  # octabromobiphenyl
            "13654-09-6",  # decabromobiphenyl
        ),
        max_percent=0.1,
        is_group=True,
        aliases=(
            "polybrominated biphenyls",
            "polybrominated biphenyl",
            "pbb",
            "pbbs",
            "polybrominated biphenyls pbb",
            "polybrominated biphenyls pbbs",
            "hexabromobiphenyl",
            "octabromobiphenyl",
            "decabromobiphenyl",
        ),
    ),
    RestrictedSubstance(
        key="pbde",
        name="Polybrominated diphenyl ethers",
        symbol="PBDE",
        cas_numbers=(
            "1163-19-5",   # decabromodiphenyl ether (decaBDE)
            "32534-81-9",  # pentabromodiphenyl ether, commercial mixture
            "32536-52-0",  # octabromodiphenyl ether, commercial mixture
            "5436-43-1",   # 2,2',4,4'-tetrabromodiphenyl ether (BDE-47)
            "60348-60-9",  # 2,2',4,4',5-pentabromodiphenyl ether (BDE-99)
        ),
        max_percent=0.1,
        is_group=True,
        aliases=(
            "polybrominated diphenyl ethers",
            "polybrominated diphenyl ether",
            "pbde",
            "pbdes",
            "polybrominated diphenyl ethers pbde",
            "polybrominated diphenyl ethers pbdes",
            "decabromodiphenyl ether",
            "pentabromodiphenyl ether",
            "octabromodiphenyl ether",
            "decabde",
            "pentabde",
            "octabde",
            "deca bde",
            "penta bde",
            "octa bde",
        ),
    ),
    RestrictedSubstance(
        key="dehp",
        name="Bis(2-ethylhexyl) phthalate",
        symbol="DEHP",
        cas_numbers=("117-81-7",),
        max_percent=0.1,
        aliases=("dehp", "bis 2 ethylhexyl phthalate", "di 2 ethylhexyl phthalate"),
    ),
    RestrictedSubstance(
        key="bbp",
        name="Butyl benzyl phthalate",
        symbol="BBP",
        cas_numbers=("85-68-7",),
        max_percent=0.1,
        aliases=("bbp", "butyl benzyl phthalate", "benzyl butyl phthalate"),
    ),
    RestrictedSubstance(
        key="dbp",
        name="Dibutyl phthalate",
        symbol="DBP",
        cas_numbers=("84-74-2",),
        max_percent=0.1,
        aliases=("dbp", "dibutyl phthalate", "di n butyl phthalate"),
    ),
    RestrictedSubstance(
        key="dibp",
        name="Diisobutyl phthalate",
        symbol="DIBP",
        cas_numbers=("84-69-5",),
        max_percent=0.1,
        aliases=("dibp", "diisobutyl phthalate", "di iso butyl phthalate", "di isobutyl phthalate"),
    ),
)

BY_KEY: dict[str, RestrictedSubstance] = {s.key: s for s in RESTRICTED_SUBSTANCES}

_CAS_INDEX: dict[str, RestrictedSubstance] = {
    cas: s for s in RESTRICTED_SUBSTANCES for cas in s.cas_numbers
}


def normalize_name(text: str) -> str:
    """Lowercase, turn punctuation into spaces, collapse whitespace."""
    text = text.lower().replace("(", " ").replace(")", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_NAME_INDEX: dict[str, RestrictedSubstance] = {}
for _s in RESTRICTED_SUBSTANCES:
    for _alias in (_s.name, _s.symbol, *_s.aliases):
        _NAME_INDEX[normalize_name(_alias)] = _s


def normalize_cas(cas: str | None) -> str | None:
    """Return a CAS number in NNNNNNN-NN-N form, or None if not parseable."""
    if not cas:
        return None
    m = re.search(r"(\d{2,7})\s*-\s*(\d{2})\s*-\s*(\d)", cas)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def match_by_cas(cas: str | None) -> RestrictedSubstance | None:
    norm = normalize_cas(cas)
    return _CAS_INDEX.get(norm) if norm else None


def match_by_name(name: str | None) -> RestrictedSubstance | None:
    """Match a declared substance name to a restricted substance.

    Tries the full normalized name, then the text outside any parentheses,
    then the text inside them, so "Lead (Pb)", "Pb" and "lead" all match.
    """
    if not name:
        return None
    candidates = [name, re.sub(r"\(.*?\)", " ", name), *re.findall(r"\((.*?)\)", name)]
    for candidate in candidates:
        hit = _NAME_INDEX.get(normalize_name(candidate))
        if hit:
            return hit
    return None


def match_substance(name: str | None, cas: str | None) -> RestrictedSubstance | None:
    """CAS takes precedence when it is a known number; otherwise fall back to name."""
    return match_by_cas(cas) or match_by_name(name)
