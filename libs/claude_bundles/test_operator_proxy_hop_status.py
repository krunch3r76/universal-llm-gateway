"""Offline tests for the operator-proxy this-hop status card."""

from __future__ import annotations

from unittest import mock

import pytest

from claude_bundles.operator_proxy_hop_status import (
    HOP_STATUS_MARKER,
    UNSPECIFIED,
    ensure_hop_status_first,
    extract_thread_id,
    standing_handoff_text_for_prompt,
)
from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt

pytestmark = pytest.mark.offline

_SEAT = "## Mission seat map (BINDING — operator-proxy mission)\n\n| Seat | Role |\n"


def test_extract_thread_id_prefers_thread_id() -> None:
    text = "arc: 99\nthread_id: 9501\nlane: agent-bus:12\n"
    assert extract_thread_id(text) == "9501"


def test_extract_thread_id_falls_through_lane_then_arc() -> None:
    assert extract_thread_id("lane: agent-bus 9496 · persistent\n") == "9496"
    assert extract_thread_id("arc: agent-bus:6655\n") == "6655"
    assert extract_thread_id("# no id here\n") is None


def test_new_block_sits_above_seat_map_and_uses_caller_title() -> None:
    out = ensure_hop_status_first(f"{_SEAT}\n# Agent-bus lane classification gap\n")
    assert out.startswith(HOP_STATUS_MARKER)
    assert out.index(HOP_STATUS_MARKER) < out.index("## Mission seat map")
    assert "- next: Agent-bus lane classification gap" in out
    assert f"- settled: {UNSPECIFIED}" in out


def test_continuity_headers_fill_lane_live_next() -> None:
    body = (
        f"{_SEAT}\n"
        "TYPE: CONTINUITY_HANDOFF\n"
        "thread_id: 9501\n"
        "trigger: cse_age\n"
        "standing_handoff: cortex://notes/system/threads/9501-standing-handoff.md\n"
        "standing_handoff_freshness: current\n"
    )
    out = ensure_hop_status_first(body)
    assert "- lane: agent-bus:9501" in out
    assert "- live: continuity hop — cse_age" in out
    assert (
        "- next: read cortex://notes/system/threads/9501-standing-handoff.md (current)"
        in out
    )


def test_standing_handoff_sidecar_fills_unspecified_only() -> None:
    sidecar = (
        "lane: agent-bus 9501 · persistent\n"
        "\n"
        "## Settled this hop — observed\n"
        "Rank matched at turn 95.\n"
        "\n"
        "## Live — one job, queued\n"
        "**Job abc** — contract: propagate\n"
        "\n"
        "## First next act\n"
        "Harvest the propagate CLOSEOUT.\n"
    )
    out = ensure_hop_status_first(
        f"{_SEAT}\nthread_id: 9501\ntrigger: cse_age\n",
        standing_handoff_text=sidecar,
    )
    assert "- settled: Rank matched at turn 95." in out
    assert "- live: continuity hop — cse_age" in out  # prompt wins over sidecar
    assert "- next: Harvest the propagate CLOSEOUT." in out
    assert "- lane: agent-bus:9501" in out


def test_mission_bullet_renders_first_and_defaults_unspecified() -> None:
    out = ensure_hop_status_first(f"{_SEAT}\nthread_id: 9501\n")
    assert f"- mission: {UNSPECIFIED}" in out
    assert out.index("- mission:") < out.index("- settled:")


def test_mission_field_parsed_from_explicit_label() -> None:
    out = ensure_hop_status_first(
        f"{_SEAT}\nmission: Recover fleet mission continuity\n"
    )
    assert "- mission: Recover fleet mission continuity" in out


def test_mission_falls_back_to_directive_vision_line() -> None:
    body = (
        f"{_SEAT}\n"
        "TYPE: DIRECTIVE\n"
        "contract: implement\n"
        "vision: Close the agent-bus lane classification gap.\n"
    )
    out = ensure_hop_status_first(body)
    assert "- mission: Close the agent-bus lane classification gap." in out


def test_mission_explicit_label_wins_over_vision_line() -> None:
    body = f"{_SEAT}\nmission: Explicit mission wins\nvision: Should not be used\n"
    out = ensure_hop_status_first(body)
    assert "- mission: Explicit mission wins" in out


def test_mission_sidecar_heading_fills_when_prompt_silent() -> None:
    sidecar = "## Mission\nRestore lane continuity for the propagation arc.\n"
    out = ensure_hop_status_first(
        f"{_SEAT}\nthread_id: 9501\n",
        standing_handoff_text=sidecar,
    )
    assert "- mission: Restore lane continuity for the propagation arc." in out


def test_existing_block_above_seat_map_keeps_fields_and_gains_receipt() -> None:
    body = (
        f"{HOP_STATUS_MARKER}\n"
        "- settled: already bound\n"
        "- live: in flight\n"
        "- next: disposition\n"
        "- lane: agent-bus:1\n"
        "- residual: keep this extra line\n"
        f"\n{_SEAT}"
    )
    out = ensure_hop_status_first(body, standing_handoff_text="## Settled\nNO\n")
    assert "- settled: already bound" in out
    assert "- residual: keep this extra line" in out
    assert "NO" not in out
    assert "- success-condition:" in out
    assert "fetch-decision: runbook:maestro-loop skipped reason=not_in_context" in out
    assert ensure_hop_status_first(out) == out  # idempotent once receipt lines present


def test_existing_block_after_seat_map_is_hoisted() -> None:
    hop = (
        f"{HOP_STATUS_MARKER}\n"
        "- settled: hoisted\n"
        "- live: x\n"
        "- next: y\n"
        "- lane: agent-bus:2\n"
    )
    body = f"{_SEAT}\n{hop}"
    out = ensure_hop_status_first(body)
    assert out.startswith(HOP_STATUS_MARKER)
    assert out.index(HOP_STATUS_MARKER) < out.index("## Mission seat map")
    assert "- settled: hoisted" in out
    assert out.count(HOP_STATUS_MARKER) == 1


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


_SECTION_ORDER = (
    "## What is running now",
    "## Your first acts, in order",
    "## How this seat works",
    "## Standing authority",
    "## Data, not instructions",
    "## Hard refusals",
)


def test_rendered_prompt_section_order_and_unclipped_current_section() -> None:
    """Newest CURRENT section is copied whole; no rendered line ends in …."""
    long_line = "IN FLIGHT " + ("alpha " * 40).rstrip()
    assert len(long_line) > 120
    assert not long_line.endswith("…")
    sidecar = (
        "## LEG 9 — CURRENT, READ FIRST\n"
        f"{long_line}\n"
        "\n"
        "## LEG 8 — CURRENT, READ FIRST\n"
        "older section must not be copied\n"
    )
    birth = "ab" * 16
    out = ensure_operator_proxy_mission_prompt(
        f"thread_id: 9501\nsuccessor_birth_id: {birth}\n"
        "execution_id: exec-1\n"
        "cdp_dispatch_id: cdp-1\n"
        "birth_turn: 9501#3\n",
        standing_handoff_text=sidecar,
    )
    positions = [out.index(marker) for marker in _SECTION_ORDER]
    assert positions == sorted(positions)
    assert out.index("## Hard refusals") < out.index("thread_id: 9501")
    assert long_line in out
    assert "older section must not be copied" not in out
    assert "LEG 9 — CURRENT, READ FIRST" in out
    assert not any(line.endswith("…") for line in out.splitlines())
    assert f"successor_birth_id {birth}" in out
    assert "execution_id exec-1" in out
    assert "CDP generate cdp-1 (9501#3)" in out


def test_missing_fields_render_unknown() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    assert "successor_birth_id unknown" in out
    assert "execution_id unknown" in out
    assert "CDP generate unknown (unknown)" in out
    assert "written_sha256 unknown" in out
    assert "\nunknown\n" in out


def test_first_acts_are_numbered_in_order() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    start = out.index("## Your first acts, in order")
    end = out.index("## How this seat works")
    block = out[start:end]
    assert "1. Use the cdp-operator-proxy" in block
    assert "maestro-loop.md in full" in block
    assert "2. Read" in block
    assert block.index("1. Use") < block.index("2. Read")
    assert block.index("2. Read") < block.index("7. Act")


def test_mission_ensure_opens_on_successor_template() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    assert out.startswith("# Hop on agent-bus:")
    assert out.index("## What is running now") < out.index("## Hard refusals")
    assert "# Mission\nDo the thing." in out


def test_mission_ensure_idempotent_with_this_hop() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.rstrip("\n") == once.rstrip("\n")
    assert once.count("# Hop on agent-bus:") == 1
    assert once.count("## Hard refusals") == 1


def test_hop_block_echoes_successor_birth_id_from_prompt() -> None:
    birth = "abcdefabcdefabcdefabcdefabcdefab"
    body = f"{_SEAT}\nsuccessor_birth_id: {birth}\nTYPE: CONTINUITY_HANDOFF\n"
    out = ensure_hop_status_first(body)
    start = out.index(HOP_STATUS_MARKER)
    end = out.index("## Mission seat map")
    block = out[start:end]
    assert "- first-acts: read cortex://notes/runbooks/maestro-loop.md § Steps" in block
    assert "lane-act-gates" in block
    assert "TYPE: SEAT_REGISTRATION quoting successor_birth_id" in block
    assert f"- successor_birth_id: {birth}" in block
    rule_line = (
        "- rule-plus-specimen: DISPOSITION over 2000 characters is refused "
        "(over_briefing_target). Specimen: 2129 and 2105 chars after the seat had read "
        "the Refuse line (a:36836)."
    )
    assert rule_line in block
    assert block.index("- first-acts:") < block.index(f"- successor_birth_id: {birth}")
    assert block.index(f"- successor_birth_id: {birth}") < block.index(
        "- rule-plus-specimen:"
    )
    assert block.index("fetch-decision:") < block.index("- first-acts:")


def test_hop_block_successor_birth_id_absent_without_header() -> None:
    with mock.patch("hop_handoff.body.mint_successor_birth_id") as mint:
        out = ensure_hop_status_first(f"{_SEAT}\nthread_id: 9501\n")
        mint.assert_not_called()
    assert "- successor_birth_id: absent" in out
    assert "- first-acts: read cortex://notes/runbooks/maestro-loop.md § Steps" in out
