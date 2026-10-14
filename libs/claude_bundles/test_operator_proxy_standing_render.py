"""Offline tests for standing-handoff checkpoint and house-rules render."""

from __future__ import annotations

import re

import pytest

from claude_bundles.operator_proxy_standing_render import (
    assess_render_budget_and_identity,
    render_checkpoint_block,
    render_house_rules_block,
)

pytestmark = pytest.mark.offline


def test_ac2_oversize_bullet_is_named_in_the_flag() -> None:
    rules = "## HOUSE RULES\n- K40 " + ("q" * 900) + "\n"
    text, exceeded = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=rules,
    )
    assert exceeded
    assert re.search(
        r"\[rules truncated: K40 — read cortex://notes/system/threads/12286-house-rules.md\]",
        text,
    )


def test_ac3_budget_loop_flags_each_shortened_section() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        + ("p" * 200)
        + "\n### LAST LEG\n"
        + ("L" * 1400)
        + "\n### OWED / NEXT\n"
        + ("O" * 1400)
        + "\n### OPEN FOR KAYWAN\n"
        + ("K" * 500)
        + "\n### NOTES\n"
        + ("u" * 7500)
        + "\n"
    )
    body, meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
    )
    for name in ("LAST LEG", "OWED / NEXT", "OPEN FOR KAYWAN"):
        assert f"[budget_exceeded {name} " in body
    assert meta["budget"] == "exceeded"
    assert body.count("[budget_exceeded LAST LEG ") >= 1


def test_ac4_intro_paragraph_is_rendered() -> None:
    rules = "## HOUSE RULES\nThis intro must stay visible.\n- K1 short rule\n"
    text, _exceeded = render_house_rules_block(
        lane="1",
        inline_rules=None,
        house_rules_text=rules,
    )
    assert "This intro must stay visible." in text


def test_ac4_numbered_items_are_bullets_and_flagged() -> None:
    rules = "## HOUSE RULES\n" + "\n".join(
        f"{number}. " + ("n" * 700) for number in (1, 2)
    )
    text, exceeded = render_house_rules_block(
        lane="1",
        inline_rules=None,
        house_rules_text=rules,
    )
    assert exceeded
    match = re.search(r"\[rules truncated: ([^\]]+) —", text)
    assert match is not None
    idents = [part.strip() for part in match.group(1).split(",")]
    assert idents == ["2", "1"]


def test_ac4_truncation_is_a_contiguous_oldest_suffix() -> None:
    lines = ["## HOUSE RULES"]
    for number in range(40, 24, -1):
        lines.append(f"- K{number} " + ("a" * 500))
    lines.append("- K18 " + ("b" * 5000))
    lines.append("- K1 " + ("c" * 40) + " OLDEST-TOKEN")
    text, _exceeded = render_house_rules_block(
        lane="1",
        inline_rules=None,
        house_rules_text="\n".join(lines) + "\n",
    )
    assert "OLDEST-TOKEN" not in text
    assert "bbbb" not in text
    match = re.search(r"\[rules truncated: ([^\]]+) —", text)
    assert match is not None
    idents = [part.strip() for part in match.group(1).split(",")]
    assert idents[0] == "K1"
    assert "K18" in idents
    assert idents.index("K1") < idents.index("K18")


def test_ac5_legacy_sections_appear_once_and_rules_are_omitted() -> None:
    sidecar = (
        "# Title\n"
        "intro-once\n"
        "## Section A\n"
        "alpha-once\n"
        "### HOUSE RULES\n"
        "rules-should-vanish\n"
        "## Section B\n"
        "beta-once\n"
    )
    body, _meta = render_checkpoint_block(
        sidecar,
        lane="9",
        inline_house_rules="rules-should-vanish",
    )
    assert body.count("intro-once") == 1
    assert body.count("alpha-once") == 1
    assert body.count("beta-once") == 1
    assert "rules-should-vanish" not in body


def test_ac18_registration_id_in_leg_prose_is_not_the_header() -> None:
    sidecar = (
        "# Title\n"
        "header has no registration\n"
        "## LAST LEG\n"
        "the narrative mentions registration_id X in passing\n"
    )
    _body, meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
        superseded_registration_id="reg-old",
    )
    _budget, identity = assess_render_budget_and_identity(
        sidecar,
        lane="12286",
        superseded_registration_id="reg-old",
    )
    assert identity == "unknown"
    assert meta["identity"] == "unknown"
    assert "checkpoint_stale" not in _body


def test_ac2_unicode_decode_on_rules_reader_falls_back_inline() -> None:
    def _raise(_uri: str) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules="### HOUSE RULES\n- K1 INLINE-RULES-SENTINEL",
        read_cortex_uri=_raise,
    )
    assert "INLINE-RULES-SENTINEL" in text
    assert "[rules_file_unreadable: UnicodeDecodeError" in text
    assert "[render_failed house_rules" not in text
    assert truncated is False


def test_ac5_prose_registration_id_is_not_checkpoint_author() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        "LEG 241 at HOP ~11:00Z — seat 36f73e56; birth 36f73e56f127\n"
        "DONE: predecessor hop · registration_id a0af518d57e84064bc86d0ee53e74636 "
        "(relayed)\n"
        "### OWED / NEXT\nok\n"
    )
    body, meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
        superseded_registration_id="reg-old",
    )
    assert meta["identity"] == "unknown"
    assert "checkpoint_stale" not in body


def test_ac5_live_leg_line_registration_id_matches() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        "seat = Cowork CSE https://x (relayed) · registration_id "
        "a0af518d57e84064bc86d0ee53e74636 (relayed)\n"
        "### OWED / NEXT\nok\n"
    )
    _body, meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
        superseded_registration_id="a0af518d57e84064bc86d0ee53e74636",
    )
    assert meta["identity"] == "match"
