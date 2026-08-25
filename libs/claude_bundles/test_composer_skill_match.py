"""Hermetic tests for Cowork Skills-list label matching (friction a:30502)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.bundle_description import adapt_skill_md_for_claude_ai
from claude_bundles.composer_skill_match import (
    attach_h1_error,
    collapse_separators,
    first_h1,
    label_matches_slug,
    normalize_first_h1,
    title_from_slug,
)
from claude_bundles.resolver import (
    claude_ai_target_slugs,
    render_bundle,
    resolve_sot,
    surface_class_for_slug,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]

_LITERARY_H1_MISSES = (
    ("life-to-code-request-lane", "Life→Code Request Lane"),
    ("fs", "Skill: MCP fs Tool"),
    ("session-close-kernel", "Session Close — Trigger + Editorial Card"),
    ("srm", "Scientific Reasoning Mode (SRM)"),
    (
        "document-review-timeline-linkage-audit",
        "Document review, timeline, and assertion-linkage audit",
    ),
)


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


def test_normalize_rewrites_literary_misses() -> None:
    for slug, literary in _LITERARY_H1_MISSES:
        assert not label_matches_slug(slug, literary)
        body, changed = normalize_first_h1(slug, f"# {literary}\n\nBody.\n")
        assert changed
        title = first_h1(body)
        assert title is not None
        assert label_matches_slug(slug, title)
        assert literary in title


def test_normalize_leaves_reachable_h1_alone() -> None:
    body, changed = normalize_first_h1(
        "cdp-operator-proxy",
        "# CDP Operator Proxy — operator seat protocol\n",
    )
    assert changed is False
    assert body.startswith("# CDP Operator Proxy")


def test_title_from_slug_is_collapse_equal() -> None:
    assert title_from_slug("life-to-code-request-lane") == "Life to code request lane"
    assert label_matches_slug(
        "life-to-code-request-lane", title_from_slug("life-to-code-request-lane")
    )


def test_customize_bytes_are_attach_reachable() -> None:
    """Zoom-out: rendered / adapted Customize bytes match, not raw SOT H1."""
    misses: list[str] = []
    for slug in claude_ai_target_slugs():
        path, _label = resolve_sot(slug, _REPO)
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        if surface_class_for_slug(slug) == "life_local":
            shipped, _ = adapt_skill_md_for_claude_ai(raw, slug=slug)
        else:
            shipped = render_bundle(slug, raw)
        err = attach_h1_error(slug, shipped)
        if err:
            misses.append(err)
    assert misses == []
