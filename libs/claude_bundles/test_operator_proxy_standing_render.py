"""Offline tests for standing-handoff checkpoint and house-rules render."""

from __future__ import annotations

import re

import pytest

from claude_bundles.operator_proxy_standing_render import (
    RULES_BUDGET_B,
    SECTION_OWED_CAP_B,
    assess_render_budget_and_identity,
    render_checkpoint_block,
    render_house_rules_block,
)

pytestmark = pytest.mark.offline


def _assert_rules_output_within_budget(text: str) -> None:
    assert len(text.encode("utf-8")) <= RULES_BUDGET_B


def test_ac2_oversize_bullet_is_dropped_whole_and_named_in_the_flag() -> None:
    rules = "## HOUSE RULES\n- K40 " + ("q" * (RULES_BUDGET_B + 64)) + "\n"
    text, exceeded = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=rules,
    )
    _assert_rules_output_within_budget(text)
    assert exceeded
    assert ("q" * 100) not in text
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
    _assert_rules_output_within_budget(text)
    assert "This intro must stay visible." in text


def test_ac4_intro_over_600_bytes_renders_whole() -> None:
    intro = "I" * 650
    rules = f"## HOUSE RULES\n{intro}\n- K1 tail\n"
    text, exceeded = render_house_rules_block(
        lane="1",
        inline_rules=None,
        house_rules_text=rules,
    )
    _assert_rules_output_within_budget(text)
    assert not exceeded
    assert intro in text
    assert "K1 tail" in text


def test_ac4_intro_over_budget_is_dropped_whole_with_intro_in_flag() -> None:
    intro = "H" * (RULES_BUDGET_B + 32)
    rules = f"## HOUSE RULES\n{intro}\n- K1 never\n"
    text, exceeded = render_house_rules_block(
        lane="1",
        inline_rules=None,
        house_rules_text=rules,
    )
    _assert_rules_output_within_budget(text)
    assert exceeded
    assert intro not in text
    assert "never" not in text
    assert "intro" in text
    assert re.search(r"\[rules truncated: .*\bintro\b", text)


def test_ac4_numbered_items_drop_whole_when_they_do_not_fit() -> None:
    rules = (
        "## HOUSE RULES\n1. "
        + ("x" * (RULES_BUDGET_B - 8))
        + "\n2. "
        + ("n" * 700)
        + "\n"
    )
    text, exceeded = render_house_rules_block(
        lane="1",
        inline_rules=None,
        house_rules_text=rules,
    )
    _assert_rules_output_within_budget(text)
    assert exceeded
    match = re.search(r"\[rules truncated: ([^\]]+) —", text)
    assert match is not None
    idents = [part.strip() for part in match.group(1).split(",")]
    assert idents == ["2", "1"]
    assert ("x" * 100) not in text
    assert ("n" * 100) not in text


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
    _assert_rules_output_within_budget(text)
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


def test_friction_39304_flag_lookahead_pops_last_row_when_flag_would_exceed_budget() -> (
    None
):
    """K2 fills the body to ~8160 B; K1 is 200 B — flag room pops K2 before K1."""
    k2_body = "b" * (8160 - len(b"## HOUSE RULES\n- K2 "))
    rules = f"## HOUSE RULES\n- K2 {k2_body}\n- K1 {'a' * 200}\n"
    text, exceeded = render_house_rules_block(
        lane="39304",
        inline_rules=None,
        house_rules_text=rules,
    )
    _assert_rules_output_within_budget(text)
    assert exceeded
    assert "aaaa" not in text
    assert k2_body not in text
    match = re.search(r"\[rules truncated: ([^\]]+) —", text)
    assert match is not None
    idents = [part.strip() for part in match.group(1).split(",")]
    assert idents == ["K1", "K2"]


def test_friction_39304_bullet_over_600_bytes_renders_whole() -> None:
    k35 = "- K35 " + ("r" * 620) + " sets EMAIL_BRIDGE_SOCK=/nonexistent (report)."
    rules = f"## HOUSE RULES\n{k35}\n- K1 tail\n"
    text, exceeded = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=rules,
    )
    _assert_rules_output_within_budget(text)
    assert len(k35.encode("utf-8")) > 600
    assert not exceeded
    assert k35 in text
    assert "[rules truncated:" not in text


def test_friction_39304_unreadable_line_reserved_in_rules_budget() -> None:
    def _raise(_uri: str) -> str:
        raise OSError("unittest")

    near_budget = "y" * 8050
    rules = f"## HOUSE RULES\n- K1 {near_budget}\n"
    text, _truncated = render_house_rules_block(
        lane="12286",
        inline_rules=rules,
        read_cortex_uri=_raise,
    )
    _assert_rules_output_within_budget(text)
    assert len(near_budget.encode("utf-8")) > 8000
    assert "[rules_file_unreadable: OSError" in text
    assert near_budget in text


def test_live_12286_house_rules_k35_k36_k37_render_whole() -> None:
    from claude_bundles.operator_proxy_hop_status import read_cortex_uri_from_root

    uri = "cortex://notes/system/threads/12286-house-rules.md"
    raw = read_cortex_uri_from_root(uri)
    if raw is None:
        pytest.skip("12286-house-rules.md not reachable from CORTEX_FILES_ROOT")
    text, _exceeded = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=raw,
    )
    _assert_rules_output_within_budget(text)
    assert "EMAIL_BRIDGE_SOCK=/nonexistent" in text
    assert "until he rules on the numbers" in text
    assert "sign from=maestro" in text


def test_ac2_unicode_decode_on_rules_reader_falls_back_inline() -> None:
    def _raise(_uri: str) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules="### HOUSE RULES\n- K1 INLINE-RULES-SENTINEL",
        read_cortex_uri=_raise,
    )
    _assert_rules_output_within_budget(text)
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


def _current_leg_atx_count(body: str) -> int:
    return len(re.findall(r"^##\s+CURRENT LEG", body, flags=re.MULTILINE))


def test_friction_39269_checkpoint_current_leg_heading_once() -> None:
    sidecar = "## CURRENT LEG\n### OWED / NEXT\npin-heading-once\n"
    body, _meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
    )
    assert body.startswith("## CURRENT LEG (rendered checkpoint)\n")
    assert _current_leg_atx_count(body) == 1
    assert "pin-heading-once" in body


def test_friction_39269_stale_checkpoint_current_leg_heading_once() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        "seat = Cowork · registration_id written-reg\n"
        "### OWED / NEXT\nstale-pin\n"
    )
    body, meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
        superseded_registration_id="predecessor-reg",
    )
    assert meta["identity"] == "stale"
    assert body.splitlines()[1].startswith("checkpoint_stale:")
    assert _current_leg_atx_count(body) == 1
    assert "stale-pin" in body


def test_friction_39269_over_cap_section_current_leg_heading_once() -> None:
    payload = "x" * (SECTION_OWED_CAP_B + 64)
    sidecar = f"## CURRENT LEG\n### OWED / NEXT\n{payload}\n"
    body, meta = render_checkpoint_block(
        sidecar,
        lane="12286",
        inline_house_rules=None,
    )
    assert meta["budget"] == "exceeded"
    assert any(
        flag.startswith("[budget_exceeded OWED / NEXT") for flag in meta["flags"]
    )
    assert "budget_exceeded" in body.splitlines()[1]
    assert _current_leg_atx_count(body) == 1


def test_friction_39269_act2_line_has_no_leading_ordinal() -> None:
    from claude_bundles.operator_proxy_standing_render import maestro_runbook_act2_line

    line = maestro_runbook_act2_line()
    assert not line.startswith("2. ")


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
