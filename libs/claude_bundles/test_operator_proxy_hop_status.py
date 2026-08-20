"""Offline tests for the operator-proxy this-hop status card."""

from __future__ import annotations

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


def test_existing_block_above_seat_map_is_byte_stable() -> None:
    body = (
        f"{HOP_STATUS_MARKER}\n"
        "- settled: already bound\n"
        "- live: in flight\n"
        "- next: disposition\n"
        "- lane: agent-bus:1\n"
        "- residual: keep this extra line\n"
        f"\n{_SEAT}"
    )
    assert ensure_hop_status_first(body) == body
    assert (
        ensure_hop_status_first(body, standing_handoff_text="## Settled\nNO\n") == body
    )


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


def test_mission_ensure_opens_with_this_hop_then_seat_map() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    chips_end = out.index(HOP_STATUS_MARKER)
    assert out.startswith("/cdp-operator-proxy\n")
    assert chips_end > 0
    assert out.index(HOP_STATUS_MARKER) < out.index("## Mission seat map (BINDING")
    assert "- next: Do the thing." in out


def test_mission_ensure_idempotent_with_this_hop() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.rstrip("\n") == once.rstrip("\n")
    assert once.count(HOP_STATUS_MARKER) == 1
