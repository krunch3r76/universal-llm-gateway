"""Cowork Skills-list label matching for composer + → Skills attach.

Customize list rows often show the SKILL.md H1, not the kebab slug. Start-anchored
hyphen-to-space regexes miss mixed labels (friction a:30502 — H1
``Life operator do-chain`` vs slug ``life-operator-do-chain``). Collapse
separators, then compare; keep start-anchor so a suffix superstring cannot steal
the click.
"""

from __future__ import annotations

import re

_PUA_RE = re.compile(r"[\ue000-\uf8ff]")
_SEP_RE = re.compile(r"[-_/\s→—–]+")


def strip_pua(label: str) -> str:
    """Drop Private Use Area glyphs (Cowork menu chevrons) from a label."""
    return _PUA_RE.sub("", label).strip()


def collapse_separators(text: str) -> str:
    """Fold hyphen/underscore/slash/dash/arrow/space to one space; casefold."""
    return _SEP_RE.sub(" ", strip_pua(text)).strip().casefold()


def label_matches_slug(slug: str, label: str) -> bool:
    """True when a Cowork Skills-list label names this slug.

    Collapsed haystack equals the collapsed slug, or starts with
    ``{slug} `` so subtitles stay legal (``CDP Operator Proxy — …``).
    Suffix superstrings (``meta-reasoning-posture-draft`` vs
    ``reasoning-posture``) do not match.
    """
    needle = collapse_separators(slug)
    hay = collapse_separators(label)
    if not needle or not hay:
        return False
    return hay == needle or hay.startswith(f"{needle} ")
