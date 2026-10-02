"""Offline tests for hop-successor standing-handoff section copy."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from claude_bundles.operator_proxy_hop_status import (
    MISSING_HANDOFF_INSTRUCTION,
    extract_thread_id,
    handoff_file_sha256,
    newest_current_section,
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
    "## Your first acts, in order",
    "## How this seat works",
    "## Standing authority",
    "## Data, not instructions",
    "## Hop request (data)",
    "## Hard refusals",
)


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


def test_fixture_head_copies_leg_11_10_and_9_then_stops() -> None:
    """Real handoff head: LEG 11, LEG 10, LEG 9; stop at LEG 9's supersedes."""
    sidecar = _HANDOFF_HEAD.read_text(encoding="utf-8")
    assert sidecar.startswith("## LEG 11 ")
    assert "## LEG 10 " in sidecar
    assert "## LEG 9 " in sidecar
    assert "## LEG 8 " in sidecar
    heading, verbatim = newest_current_section(sidecar) or ("", "")
    assert heading.startswith("LEG 11 ")
    assert "LEG 11 ~21:00Z" in verbatim
    assert "LEG 10 ~20:55Z" in verbatim
    assert "LEG 9 ~20:50Z" in verbatim
    assert "Do NOT re-admit until 14611 closes" in verbatim
    assert "supersedes LEG 8 IN FLIGHT" in verbatim
    assert verbatim.index("LEG 11 ~21:00Z") < verbatim.index("LEG 10 ~20:55Z")
    assert verbatim.index("LEG 10 ~20:55Z") < verbatim.index("LEG 9 ~20:50Z")
    assert "LEG 8 ~20:45Z" not in verbatim
    assert "consolidated section not found" not in verbatim
    birth = "ab" * 16
    out = ensure_operator_proxy_mission_prompt(
        f"thread_id: 12286\nsuccessor_birth_id: {birth}\n",
        standing_handoff_text=sidecar,
        execution_id="exec-from-satellite",
    )
    assert f"handoff file sha256 at render {handoff_file_sha256(sidecar)}" in out
    assert handoff_file_sha256(sidecar) == hashlib.sha256(sidecar.encode()).hexdigest()
    assert "written_sha256" not in out.split("## Your first acts", 1)[0]
    assert "execution_id exec-from-satellite" in out
    assert "CDP generate" not in out
    positions = [out.index(marker) for marker in _SECTION_ORDER]
    assert positions == sorted(positions)
    assert out.index("## Hop request (data)") < out.index("thread_id: 12286")
    assert out.index("thread_id: 12286") < out.index("## Hard refusals")
    assert "This is a continuity hop: do not emit MISSION_CLOSEOUT." in out


def test_section_cap_adds_the_read_the_head_line() -> None:
    parts = [
        f"## LEG {number} — CURRENT, READ FIRST\nitem {number}\n"
        for number in range(5, 0, -1)
    ]
    sidecar = "\n".join(parts)
    heading, verbatim = newest_current_section(sidecar) or ("", "")
    assert heading.startswith("LEG 5")
    assert "LEG 2 — CURRENT" in verbatim
    assert "LEG 1 — CURRENT" not in verbatim
    assert verbatim.endswith("consolidated section not found; read the handoff head")


def test_missing_handoff_renders_the_author_instruction() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    assert MISSING_HANDOFF_INSTRUCTION in out
    assert "Author the standing handoff before you leave." in out
    assert "execution_id unknown" not in out
    assert "CDP generate" not in out
    assert "successor_birth_id unknown" in out
    running = out.split("## Your first acts", 1)[0]
    assert "handoff file sha256 at render" not in running
    assert "standing handoff file absent" in running


def test_empty_execution_id_omits_the_clause_even_when_prompt_names_one() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 1\nexecution_id: from-prompt\n"
        "cdp_dispatch_id: cdp-1\nbirth_turn: 1#3\n",
        execution_id="",
    )
    assert "execution_id" not in out.split("## What is running now", 1)[0]
    assert "from-prompt" not in out.split("## Hop request (data)", 1)[0]
    assert "CDP generate" not in out
    assert "1#3" in out  # caller body, under the hop-request heading


def test_rendered_prompt_section_order_and_unclipped_current_section() -> None:
    """One long CURRENT line is copied whole; no rendered line ends in …."""
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
        f"thread_id: 9501\nsuccessor_birth_id: {birth}\n",
        standing_handoff_text=sidecar,
        execution_id="exec-1",
    )
    positions = [out.index(marker) for marker in _SECTION_ORDER]
    assert positions == sorted(positions)
    assert out.index("## Hop request (data)") < out.index("thread_id: 9501")
    assert out.index("thread_id: 9501") < out.index("## Hard refusals")
    assert long_line in out
    assert "older section must not be copied" not in out
    assert "LEG 9 — CURRENT, READ FIRST" in out
    assert not any(line.endswith("…") for line in out.splitlines())
    assert f"successor_birth_id {birth}" in out
    assert "execution_id exec-1" in out


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
    assert out.index("## Hop request (data)") < out.index("# Mission\nDo the thing.")
    assert out.index("# Mission\nDo the thing.") < out.index("## Hard refusals")


def test_mission_ensure_idempotent_with_this_hop() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.rstrip("\n") == once.rstrip("\n")
    assert once.count("# Hop on agent-bus:") == 1
    assert once.count("## Hard refusals") == 1
    assert once.count("## Hop request (data)") == 1
