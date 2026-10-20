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
    assert "never" in text
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

    # 8070 B body + "\n" + 121 B unreadable line = 8192 at n=8050; n=8100 only
    # fits when the line is NOT reserved, so pre-fix code overflows to 8242 B.
    near_budget = "y" * 8100
    rules = f"## HOUSE RULES\n- K1 {near_budget}\n"
    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules=rules,
        read_cortex_uri=_raise,
    )
    _assert_rules_output_within_budget(text)
    assert truncated
    assert near_budget not in text
    assert "[rules truncated: K1 " in text
    assert "[rules_file_unreadable: OSError" in text


def test_bullet_ident_leading_token_widens_k23_is_not_k23() -> None:
    from claude_bundles.operator_proxy_standing_render import _bullet_ident

    assert _bullet_ident("- K23 a:39011: restarts permitted.", "") == "K23"
    assert (
        _bullet_ident(
            "- Kaywan 2026-10-09 17:40Z (Maestro chat): Widens K23.",
            "fallback",
        )
        == "fallback"
    )


def _body_within_budget(intro_len: int, target: int) -> str:
    intro = "I" * intro_len
    rows: list[str] = []
    index = 1
    body = f"## HOUSE RULES\n{intro}"
    while len(body.encode("utf-8")) < target - 80:
        row = f"- K{index} " + ("r" * 80)
        rows.append(row)
        body = f"## HOUSE RULES\n{intro}\n" + "\n".join(rows)
        index += 1
    encoded = body.encode("utf-8")
    if len(encoded) > target:
        body = encoded[:target].decode("utf-8", errors="ignore")
    return body


@pytest.mark.parametrize("target", [8100, 8192])
def test_intro_included_does_not_consume_budget_twice(target: int) -> None:
    body = _body_within_budget(500, target)
    assert len(body.encode("utf-8")) <= RULES_BUDGET_B
    assert len(body.encode("utf-8")) >= 8000
    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=body,
    )
    assert truncated is False
    assert "[rules truncated:" not in text
    assert "K1" in text


def test_headingless_intro_with_oversized_first_row_stays_within_budget() -> None:
    """Intro fits and the first row does not: the flag still has to fit.

    accept_count stays 0, so the drop loop used to return intro+flag over budget.
    """
    intro = "I" * 8180
    row = "- K1 " + ("x" * 9000)
    body = "\n".join((intro, row))
    assert not body.startswith("#")
    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=body,
    )
    assert truncated is True
    assert len(text.encode("utf-8")) <= RULES_BUDGET_B
    assert "I" * 8180 not in text


def test_intro_dropped_with_rows_at_budget_stays_within_budget() -> None:
    """A dropped intro appends a flag even when every row already fills the budget."""
    heading = "## HOUSE RULES"
    intro = "H" * (RULES_BUDGET_B + 32)
    row_len = RULES_BUDGET_B - len(heading.encode("utf-8")) - 1
    row = "- K1 " + ("r" * (row_len - len("- K1 ")))
    body = "\n".join((heading, intro, row))
    packed = "\n".join((heading, row))
    assert len(packed.encode("utf-8")) == RULES_BUDGET_B
    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=body,
    )
    assert truncated is True
    assert "intro" in text
    assert len(text.encode("utf-8")) <= RULES_BUDGET_B


def test_headingless_truncation_stays_within_budget() -> None:
    """Flag-room accounting charges the intro separator even with no heading.

    The kept row plus the truncation flag is built to be RULES_BUDGET_B + 1
    bytes. The old flag-room total omitted that separator when heading_line
    was empty, so it accepted the extra byte.
    """
    from claude_bundles.operator_proxy_standing_render import _rules_truncation_flag

    intro = "Prose standing line"
    flag = _rules_truncation_flag(
        ["K2"], "cortex://notes/system/threads/12286-house-rules.md"
    )
    kept_len = (
        RULES_BUDGET_B - len(flag.encode("utf-8")) - len(intro.encode("utf-8")) - 1
    )
    kept = "- K1 " + ("r" * (kept_len - len("- K1 ")))
    dropped = "- K2 " + ("z" * 400)
    body = "\n".join((intro, kept, dropped))
    assert not body.startswith("#")
    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=body,
    )
    assert truncated is True
    assert len(text.encode("utf-8")) <= RULES_BUDGET_B


def test_friction_39340_post_trim_live_copy_within_budget_no_truncation_flag() -> None:
    from pathlib import Path

    pin = (
        Path(__file__).resolve().parent
        / "testdata"
        / "12286-house-rules-fixture-3e492abe.txt"
    )
    raw = pin.read_text(encoding="utf-8")
    text, truncated = render_house_rules_block(
        lane="12286",
        inline_rules=None,
        house_rules_text=raw,
    )
    _assert_rules_output_within_budget(text)
    assert not truncated
    assert "[rules truncated:" not in text
    assert "EMAIL_BRIDGE_SOCK=/nonexistent" in text
    assert "Argon never authors and reviews one item" in text
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


def test_cap_utf8_for_hop_drops_the_line_that_does_not_fit() -> None:
    """A hop slot over budget keeps whole lines only (friction:39828)."""
    from claude_bundles.operator_proxy_standing_render import cap_utf8_for_hop

    body = "alpha line stays\n" + ("beta " * 30) + "\n"
    capped = cap_utf8_for_hop(body, max_bytes=80, label="pointers")
    source_lines = set(body.splitlines())
    assert capped.endswith("]")
    assert "[hop_budget_exceeded pointers " in capped
    assert len(capped.encode("utf-8")) <= 80
    for line in capped.splitlines():
        if line.startswith("[hop_budget_exceeded "):
            continue
        assert line in source_lines
    assert "alpha line stays" in capped
    assert "beta beta" not in capped


def test_cap_utf8_for_hop_does_not_slice_a_heading_or_bullet() -> None:
    from claude_bundles.operator_proxy_standing_render import cap_utf8_for_hop

    lessons = cap_utf8_for_hop("Q" * 80, max_bytes=40, label="lessons")
    assert lessons == "[hop_budget_exceeded lessons 80/40]"
    pointers = cap_utf8_for_hop(
        "- Prior LEG blocks stay whole\n"
        "- K-row rulings runbook Appendix continues well past this cap",
        max_bytes=80,
        label="pointers",
    )
    assert "Appendix" not in pointers
    assert "Prior LEG blocks stay whole" in pointers
    assert "[hop_budget_exceeded pointers " in pointers


def test_cap_utf8_for_hop_live_leg_keeps_a_word_boundary_prefix() -> None:
    from claude_bundles.operator_proxy_standing_render import cap_utf8_for_hop

    line = (
        "- lane 15722 #86 PICKUP|todo:maestro-computed-hop — "
        "R3: read DONE / FAILED / QUESTION and nothing in between"
    )
    capped = cap_utf8_for_hop(line, max_bytes=90, label="live_leg")
    assert "todo:maestro-computed-hop" in capped
    assert "nothing in between" not in capped
    assert "[hop_budget_exceeded live_leg " in capped
    assert len(capped.encode("utf-8")) <= 90


def test_cap_utf8_for_hop_live_leg_stops_before_rule_text() -> None:
    """A room that reaches into the rule must not emit a partial rule."""
    from claude_bundles.operator_proxy_standing_render import cap_utf8_for_hop

    line = (
        "- lane 15722 #86 PICKUP|todo:maestro-computed-hop — "
        "R3: read DONE / FAILED / QUESTION and nothing in between"
    )
    head = "- lane 15722 #86 PICKUP|todo:maestro-computed-hop"
    # Flag plus a room that includes the em dash and the start of R3.
    capped = cap_utf8_for_hop(line, max_bytes=len(head.encode()) + 40, label="live_leg")
    assert "todo:maestro-computed-hop" in capped
    assert "R3:" not in capped
    assert "DONE" not in capped
    assert len(capped.encode("utf-8")) <= len(head.encode()) + 40


def test_hop_house_rules_pointer_cites_handoff_when_file_is_absent() -> None:
    from claude_bundles.operator_proxy_standing_render import hop_house_rules_pointer

    text = hop_house_rules_pointer(
        "16416", rules_file_exists=False, rules_heading="HOUSE RULES"
    )
    assert 'section="HOUSE RULES"' in text
    assert "16416-standing-handoff.md" in text
    assert 'path="cortex://notes/system/threads/16416-house-rules.md"' not in text
    assert "16416-house-rules.md" in text
    assert "is absent" in text


def test_hop_house_rules_pointer_keeps_file_read_when_present_or_unprobed() -> None:
    from claude_bundles.operator_proxy_standing_render import hop_house_rules_pointer

    present = hop_house_rules_pointer(
        "12286", rules_file_exists=True, rules_heading="HOUSE RULES"
    )
    unprobed = hop_house_rules_pointer("1")
    assert 'path="cortex://notes/system/threads/12286-house-rules.md"' in present
    assert "1-house-rules.md" in unprobed
    absent = hop_house_rules_pointer("16416", rules_file_exists=False)
    assert "no HOUSE RULES section" in absent
    assert 'op="read"' not in absent
    assert 'op="md_read"' not in absent


_LANE_16416_RULES_HEADING = "HOUSE RULES (by assertion id; extend, never drop)"


def test_hop_house_rules_pointer_uses_suffixed_16416_heading() -> None:
    from claude_bundles.operator_proxy_standing_render import hop_house_rules_pointer

    text = hop_house_rules_pointer(
        "16416",
        rules_file_exists=False,
        rules_heading=_LANE_16416_RULES_HEADING,
    )
    assert f'section="{_LANE_16416_RULES_HEADING}"' in text
    assert 'section="HOUSE RULES"' not in text


def test_friction_39828_mission_pointer_and_running_now_sha() -> None:
    """Missing house-rules file is not a required read; lane overflow keeps sha."""
    from claude_bundles.operator_proxy_mission import (
        ensure_operator_proxy_mission_prompt,
    )

    sidecar = (
        "## HOUSE RULES\n- K1 the rules live in this handoff\n\n## CURRENT LEG\nLEG 1\n"
    )
    rows = [f"| {i} | Bot | {10000 + i} | box | live |" for i in range(1, 30)]
    roster = "| N | Display name | Lane | Mailbox | State |\n" + "\n".join(rows)

    def _exists(path: str) -> bool:
        return not str(path).endswith("16416-house-rules.md")

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 16416\n",
        standing_handoff_text=sidecar,
        roster_text=roster,
        fetch_turns=lambda _lane: [],
        journal_text="",
        register_text="",
        archive_path_exists=_exists,
    )
    rules = out.split("## CURRENT LEG (rendered checkpoint)", 1)[0]
    assert 'section="HOUSE RULES"' in rules
    assert "16416-standing-handoff.md" in rules
    assert 'path="cortex://notes/system/threads/16416-house-rules.md"' not in rules
    running = out.split("## What is running now", 1)[1].split("\n\n", 1)[0]
    assert "handoff sha256 " in running
    assert "[hop_budget_exceeded running_now " in running
    sha_line = running.splitlines()[0]
    assert "handoff sha256 " in sha_line
    assert "on lanes" not in sha_line
    for line in running.splitlines():
        if line.startswith("[hop_budget_exceeded "):
            continue
        assert not line.endswith(" h")


def test_friction_39828_suffixed_heading_and_probe_oserror() -> None:
    """Suffixed heading is the md_read section; a probe OSError does not fail the block."""
    from claude_bundles.operator_proxy_mission import (
        ensure_operator_proxy_mission_prompt,
    )

    sidecar = f"## {_LANE_16416_RULES_HEADING}\n- K24 stay\n\n## CURRENT LEG\nLEG 1\n"

    def _missing(_path: str) -> bool:
        return False

    def _boom(_path: str) -> bool:
        raise OSError("cortex root unreadable")

    absent = ensure_operator_proxy_mission_prompt(
        "thread_id: 16416\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=lambda _lane: [],
        journal_text="",
        register_text="",
        archive_path_exists=_missing,
    )
    rules = absent.split("## CURRENT LEG (rendered checkpoint)", 1)[0]
    assert f'section="{_LANE_16416_RULES_HEADING}"' in rules
    assert 'section="HOUSE RULES"' not in rules
    assert "is absent" in rules

    unverified = ensure_operator_proxy_mission_prompt(
        "thread_id: 16416\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=lambda _lane: [],
        journal_text="",
        register_text="",
        archive_path_exists=_boom,
    )
    rules_u = unverified.split("## CURRENT LEG (rendered checkpoint)", 1)[0]
    assert f'section="{_LANE_16416_RULES_HEADING}"' in rules_u
    assert "existence unverified" in rules_u
    assert "[render_failed house_rules:" not in unverified
