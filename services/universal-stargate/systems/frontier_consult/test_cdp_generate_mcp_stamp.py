"""CDP generate Block 5 MCP default stamping (friction a:32088)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from systems.frontier_consult.cdp_generate_mcp_stamp import stamp_cdp_packet_mcp_default
from systems.frontier_consult.handoff_web_mcp_default import (
    LIFE_ONLY_MCP_BODY,
    has_explicit_life_code_split,
)

_THIN_PACKET = """\
---
related_thread_ids: ["2235"]
contract: light-bounded
---

<scope>Review lane worktree reads.</scope>
<invariants>[scope] traces to task.</invariants>
<task_guidance>Bind forks.</task_guidance>
<corpus>artifact</corpus>
<mcp_capabilities>1. fs(read) lane file; may run pytest</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""

_THIN_MCP_OPEN = (
    "<mcp_capabilities>1. fs(read) lane file; may run pytest</mcp_capabilities>"
)
_ALREADY_SPLIT = _THIN_PACKET.replace(
    _THIN_MCP_OPEN,
    "<mcp_capabilities>LIFE/CORTEX MCP: ON — cortex(entity_get/search).\n"
    "CODE/VORTEX MCP: OFF — no workspaces or code-only tools.\n"
    "1. fs(read) lane file</mcp_capabilities>",
)


def test_stamp_cdp_packet_mcp_default_thin_packet() -> None:
    result = stamp_cdp_packet_mcp_default(prompt_text=_THIN_PACKET)
    assert result.stamped
    assert result.body is not None
    start = result.body.find("<mcp_capabilities>")
    end = result.body.find("</mcp_capabilities>")
    mcp = result.body[start + len("<mcp_capabilities>") : end]
    assert has_explicit_life_code_split(mcp)
    assert LIFE_ONLY_MCP_BODY.splitlines()[0] in mcp
    assert "CODE/VORTEX MCP: OFF" in mcp


def test_stamp_cdp_packet_mcp_default_skips_when_split_present() -> None:
    result = stamp_cdp_packet_mcp_default(prompt_text=_ALREADY_SPLIT)
    assert not result.stamped
    assert result.body is None


def test_stamp_cdp_packet_mcp_default_from_packet_path(tmp_path: Path) -> None:
    packet = tmp_path / "review.md"
    packet.write_text(_THIN_PACKET, encoding="utf-8")
    result = stamp_cdp_packet_mcp_default(packet_path=str(packet))
    assert result.stamped
    assert result.source_label == f"packet_path:{packet}"


def test_frontier_cdp_packet_enriched_event_factory() -> None:
    from systems.frontier_consult.events import FrontierCdpPacketEnriched

    event = FrontierCdpPacketEnriched(
        request_id="req-32088",
        packet_path="packet_path:tmp/reviews/foo.md",
        to_agent="web-anthropic",
        web_mcp_stamped=True,
    )
    assert event.signal == "frontier.cdp.packet.enriched"
    assert event.payload["request_id"] == "req-32088"
    assert event.payload["to_agent"] == "web-anthropic"
    assert event.payload["web_mcp_stamped"] is True


def test_stage_inputs_stamps_before_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    from systems.frontier_consult.cdp_generate import _stage_inputs

    captured: dict[str, str | None] = {}

    def fake_stage(**kwargs: object) -> object:
        captured["prompt_text"] = kwargs.get("prompt_text")
        captured["packet_path"] = kwargs.get("packet_path")
        return MagicMock(prompt_uri="cortex://notes/system/ephemeral/cdp-endpoint/x/prompt.md")

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate.stage_cdp_prompt_with_skills",
        fake_stage,
    )
    _stage_inputs(
        execution_id="exec-32088",
        prompt=_THIN_PACKET,
        sidecar_ref=None,
        packet_path=None,
        request_id="req-32088",
    )
    body = captured["prompt_text"]
    assert isinstance(body, str)
    assert has_explicit_life_code_split(body)
    assert captured["packet_path"] is None
