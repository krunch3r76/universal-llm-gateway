"""Hermetic tests for Cowork Skills-list label matching (friction a:30502)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.bundle_description import parse_frontmatter
from claude_bundles.composer_skill_match import (
    collapse_separators,
    label_matches_slug,
)
from claude_bundles.resolver import claude_ai_target_slugs, resolve_sot

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]

# H1s whose collapsed tokens are not a prefix of the slug (picker still
# needs the kebab name, or a retitle). Not the a:30502 hyphen-mix class.
_KNOWN_H1_ATTACH_MISSES = frozenset(
    {
        "document-review-timeline-linkage-audit",  # extra "assertion", comma
        "fs",  # H1 prefixed "Skill: MCP …"
        "life-to-code-request-lane",  # arrow drops the "to" token
        "session-close-kernel",  # H1 omits "kernel"
        "srm",  # acronym only in trailing "(SRM)"
    }
)


def _first_h1(text: str) -> str | None:
    _, body = parse_frontmatter(text)
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return None
    return None


def test_specimen_h1_life_operator_do_chain_matches() -> None:
    assert label_matches_slug("life-operator-do-chain", "Life operator do-chain")


def test_teach_once_h1_keeps_first_hyphen() -> None:
    assert label_matches_slug("teach-once-routine-mint", "Teach-once routine mint")


def test_subtitle_prefix_still_matches() -> None:
    assert label_matches_slug(
        "cdp-operator-proxy",
        "CDP Operator Proxy — operator seat protocol",
    )


def test_exact_slug_and_spaced_slug_match() -> None:
    assert label_matches_slug("reasoning-posture", "reasoning-posture")
    assert label_matches_slug("reasoning-posture", "Reasoning Posture")


def test_suffix_superstring_is_rejected() -> None:
    assert not label_matches_slug("reasoning-posture", "meta-reasoning-posture-draft")


def test_life_to_code_arrow_h1_drops_to_token() -> None:
    """Collapse cannot invent the dropped 'to'; kebab label still matches."""
    assert not label_matches_slug("life-to-code-request-lane", "Life→Code Request Lane")
    assert label_matches_slug("life-to-code-request-lane", "life-to-code-request-lane")


def test_empty_inputs_do_not_match() -> None:
    assert not label_matches_slug("", "Life operator do-chain")
    assert not label_matches_slug("life-operator-do-chain", "")
    assert not label_matches_slug("   ", "   ")


def test_collapse_folds_arrow_and_emdash() -> None:
    assert collapse_separators("Life→Code") == "life code"
    assert collapse_separators("Proxy — extra") == "proxy extra"


def test_claude_ai_target_h1s_are_matcher_reachable_or_known() -> None:
    """Zoom-out: every Customize target H1 either attaches or is allowlisted."""
    misses: list[str] = []
    for slug in claude_ai_target_slugs():
        path, _label = resolve_sot(slug, _REPO)
        if not path.is_file():
            continue
        h1 = _first_h1(path.read_text(encoding="utf-8"))
        if h1 is None:
            continue
        if label_matches_slug(slug, h1):
            continue
        misses.append(slug)
    unexpected = sorted(set(misses) - _KNOWN_H1_ATTACH_MISSES)
    stale = sorted(_KNOWN_H1_ATTACH_MISSES - set(misses))
    assert unexpected == [], f"new H1 attach misses: {unexpected}"
    assert stale == [], f"allowlist stale (H1 now matches): {stale}"
