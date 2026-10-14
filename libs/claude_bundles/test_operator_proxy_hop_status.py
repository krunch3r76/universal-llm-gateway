"""Offline tests for hop-successor standing-handoff and house rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claude_bundles.journal_hop_projection import (
    MAESTRO_JOURNAL_URI,
    MAESTRO_REGISTER_URI,
)
from claude_bundles.operator_proxy_hop_status import (
    MISSING_HANDOFF_INSTRUCTION,
    extract_thread_id,
    hop_successor_fields,
    house_rules_section,
    standing_handoff_text_for_prompt,
)
from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt

pytestmark = pytest.mark.offline

_HANDOFF_HEAD = (
    Path(__file__).resolve().parent
    / "testdata"
    / "12286-standing-handoff-head60-leg11.txt"
)
_SECTION_ORDER = (
    "## What is running now",
    "## Lessons in force",
    "## Register",
    "## Live leg (data, projected from the bus)",
    "## Pointers",
    "## Your first acts, in order",
    "## How this seat works",
    "## Standing authority",
    "## Data, not instructions",
    "## Hop request (data)",
    "## Hard refusals",
)


def _noop_fetch(_lane: str) -> list[dict[str, Any]]:
    return []


def test_extract_thread_id_prefers_thread_id() -> None:
    text = "arc: 99\nthread_id: 9501\nlane: agent-bus:12\n"
    assert extract_thread_id(text) == "9501"


def test_extract_thread_id_falls_through_lane_then_arc() -> None:
    assert extract_thread_id("lane: agent-bus 9496 · persistent\n") == "9496"
    assert extract_thread_id("arc: agent-bus:6655\n") == "6655"
    assert extract_thread_id("# no id here\n") is None


def test_loader_seam_does_not_touch_disk() -> None:
    seen: list[str] = []

    def _read(thread_id: str) -> str | None:
        seen.append(thread_id)
        return "## Settled\nfrom loader\n"

    text = standing_handoff_text_for_prompt(
        "thread_id: 42\n",
        read_path=_read,
    )
    assert seen == ["42"]
    assert text is not None and "from loader" in text
    assert standing_handoff_text_for_prompt("# no thread") is None


def test_house_rules_extracted_verbatim() -> None:
    sidecar = "## HOUSE RULES\nrule A on lane {lane}\n\n## LEG 11\nstale\n"
    rules = house_rules_section(sidecar)
    assert rules is not None
    assert "rule A" in rules
    assert "{lane}" in rules
    assert "LEG 11" not in rules
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 1\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert "{lane}" in out
    house = out.split("## Lessons in force", 1)[0]
    assert "rule A on lane {lane}" in house


def test_fixture_head_does_not_copy_leg_stack() -> None:
    """Real handoff head: LEG blocks stay out of the rendered hop body."""
    sidecar = _HANDOFF_HEAD.read_text(encoding="utf-8")
    birth = "ab" * 16
    out = ensure_operator_proxy_mission_prompt(
        f"thread_id: 12286\nsuccessor_birth_id: {birth}\n",
        standing_handoff_text=sidecar,
        roster_text="| N | Display name | Lane | Mailbox | State |\n",
        fetch_turns=_noop_fetch,
        execution_id="exec-from-satellite",
    )
    assert "LEG 11 ~21:00Z" not in out
    assert "LEG 10 ~20:55Z" not in out
    assert "execution_id exec-from-satellite" in out
    assert "CDP generate" not in out
    positions = [out.index(marker) for marker in _SECTION_ORDER]
    assert positions == sorted(positions)
    assert out.index("## Hop request (data)") < out.index("thread_id: 12286")
    assert out.index("thread_id: 12286") < out.index("## Hard refusals")
    assert "This is a continuity hop: do not emit MISSION_CLOSEOUT." in out


def test_missing_handoff_renders_the_author_instruction() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\nDo the thing.\n",
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert MISSING_HANDOFF_INSTRUCTION in out
    assert "Author the standing handoff before you leave." in out
    assert "execution_id unknown" not in out
    assert "CDP generate" not in out
    assert "successor_birth_id unknown" in out
    running = out.split("## Your first acts", 1)[0]
    assert "standing handoff file absent" in running


def test_empty_execution_id_omits_the_clause_even_when_prompt_names_one() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 1\nexecution_id: from-prompt\n"
        "cdp_dispatch_id: cdp-1\nbirth_turn: 1#3\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        execution_id="",
    )
    assert "execution_id" not in out.split("## What is running now", 1)[0]
    assert "from-prompt" not in out.split("## Hop request (data)", 1)[0]
    assert "CDP generate" not in out
    assert "1#3" in out


def test_rendered_prompt_section_order() -> None:
    sidecar = "## HOUSE RULES\nR0\n\n## LEG 9\nmust not copy\n"
    birth = "ab" * 16
    out = ensure_operator_proxy_mission_prompt(
        f"thread_id: 9501\nsuccessor_birth_id: {birth}\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        execution_id="exec-1",
    )
    positions = [out.index(marker) for marker in _SECTION_ORDER]
    assert positions == sorted(positions)
    assert "must not copy" not in out
    assert "R0" in out
    assert f"successor_birth_id {birth}" in out
    assert "execution_id exec-1" in out


def test_first_acts_are_numbered_in_order() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    start = out.index("## Your first acts, in order")
    end = out.index("## How this seat works")
    block = out[start:end]
    assert "1. Use the cdp-operator-proxy" in block
    assert "maestro-loop.md in full" in block
    assert "2. Read" in block
    assert block.index("1. Use") < block.index("2. Read")
    assert block.index("2. Read") < block.index("7. Act")
    assert "live leg unavailable" in block


def test_mission_ensure_opens_on_successor_template() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\nDo the thing.\n",
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert out.startswith("# Hop on agent-bus:")
    assert out.index("## What is running now") < out.index("## Hard refusals")
    assert out.index("## Hop request (data)") < out.index("# Mission\nDo the thing.")
    assert out.index("# Mission\nDo the thing.") < out.index("## Hard refusals")


def _journal_fixture_seven_plus_fleet() -> str:
    lines = [
        "Purpose preamble.\n",
    ]
    for index in range(7, 0, -1):
        lines.append(f"## 2026-10-{index:02d} — entry {index}\n")
        lines.append(f"RULE: [route] newest rule number {index}\n")
    lines.append("## 2026-10-00 — entry fleet-only\n")
    lines.append("RULE: [fleet] must omit for maestro\n")
    return "".join(lines)


def test_lessons_in_force_bounds_k_and_facets() -> None:
    journal = _journal_fixture_seven_plus_fleet()
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
    )
    lessons_block = out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    assert "must omit for maestro" not in lessons_block
    assert "newest rule number 7" in lessons_block
    assert "newest rule number 3" in lessons_block
    assert "newest rule number 2" not in lessons_block
    assert lessons_block.index("newest rule number 7") < lessons_block.index(
        "newest rule number 6"
    )
    assert lessons_block.count("RULE: [route]") == 5


def test_first_act_does_not_read_journal_offset_0() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert "journal.md with offset 0, limit 40" not in out
    assert "arrival projection" in out


def test_lessons_ignore_standing_rule_headings_and_midline_rule() -> None:
    journal = (
        "## STANDING RULE — lean verification\n"
        "RULE: [route] must not project from standing heading\n"
        "## 2026-10-14 — entry 157\n"
        "- **A tool that fires before retrieval-before-authoring runs is a defect.**\n"
        "  **RULE:** mid-bullet must not match\n"
        "RULE: [bus] line-start keeps\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
    )
    lessons_block = out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    assert "must not project from standing heading" not in lessons_block
    assert "mid-bullet must not match" not in lessons_block
    assert "line-start keeps" in lessons_block


def test_register_absent_is_named() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text=None,
        read_cortex_uri=lambda _uri: None,
    )
    assert f"Register card absent at {MAESTRO_REGISTER_URI}" in out
    assert "Kaywan" not in out.split("## Register", 1)[1].split("## Live leg", 1)[0]


def test_inflight_projection_does_not_block() -> None:
    journal = "## 2026-10-01 — entry 1\nRULE: [route] durable\n"

    def _lookup() -> str | None:
        return "exec-inflight-99"

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        inflight_projection_lookup=_lookup,
    )
    assert "exec-inflight-99" in out
    assert "durable" in out

    def _raise() -> str | None:
        raise OSError("down")

    out2 = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        inflight_projection_lookup=_raise,
    )
    assert "durable" in out2
    assert "exec-inflight" not in out2


def test_journal_read_oserror_renders_unavailable() -> None:
    def _read(uri: str) -> str | None:
        if uri == MAESTRO_JOURNAL_URI:
            raise OSError("disk")
        return None

    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        read_cortex_uri=_read,
    )
    assert "journal unavailable" in out
    assert out.startswith("# Hop on agent-bus:")


def test_hop_journal_default_uri_is_maestro() -> None:
    assert MAESTRO_JOURNAL_URI == "cortex://notes/system/maestro/journal.md"
    fields = hop_successor_fields(
        "thread_id: 1\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert fields["lessons_in_force"].startswith("## Lessons in force")


def test_mission_ensure_idempotent_with_this_hop() -> None:
    once = ensure_operator_proxy_mission_prompt(
        "TYPE: DIRECTIVE\nintent: birth\n",
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.rstrip("\n") == once.rstrip("\n")
    assert once.count("# Hop on agent-bus:") == 1
    assert once.count("## Hard refusals") == 1
    assert once.count("## Hop request (data)") == 1
