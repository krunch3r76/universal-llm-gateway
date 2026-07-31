"""Regression: web/lead auto_inject ref-only manifest (boot-card de-inline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from response_size_guard import _DEFAULT_THRESHOLD, _measure_result

from tools._boot_helpers._manifest import build_auto_inject_skills_ref, build_manifest
from tools.cortex_named_tools import (
    _boot_audit_dump as boot_audit_dump,
)
from tools.cortex_named_tools import (
    _boot_runner as boot_runner,
)
from tools.cortex_named_tools import (
    _orchestration_tools as orchestration_tools,
)

_LARGE_INJECT_BODY = (
    "<!-- cortex:invariant-skills-autoappend sha256=abc count=2 -->\n"
    + ("x" * 50_000)
)
_STUB_INJECTED = [
    {"id": "agent_skill:orchestrator-core", "digest": "d1", "bytes": 1000},
    {"id": "agent_skill:model-tier-awareness-web", "digest": "d2", "bytes": 900},
]
_WIRE_HEADROOM_BUDGET = 110 * 1024


class _DummyMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, title: str) -> Any:  # noqa: ARG002
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _stub_extract_boot_results(
    _agent: str, _raw: dict[str, Any], _profile: dict[str, Any]
) -> dict[str, Any]:
    from tools._boot_extract_stub import stub_extract_boot_results

    out = stub_extract_boot_results(_agent, _raw, _profile)
    out["skills"] = [{"slug": "advisor-timing"}]
    out["skills_concise_markdown"] = "## Skills\n- advisor-timing"
    out["skills_card_markdown"] = "## Agent Skills\n> Load on demand"
    return out


def _install_boot_stubs(
    monkeypatch: Any, tmp_path: Path, *, stub_auto_inject: bool = True
) -> None:
    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        boot_runner,
        "build_futures_spec",
        lambda _agent, _profile, _recorder: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner, "extract_boot_results", _stub_extract_boot_results
    )
    monkeypatch.setattr(boot_runner, "build_unread_threads", lambda _threads: [])
    monkeypatch.setattr(boot_runner, "build_review_top", lambda _items: [])
    monkeypatch.setattr(
        boot_runner,
        "render_operational_context",
        lambda **_kwargs: "operational context stub",
    )
    monkeypatch.setattr(
        boot_runner,
        "render_briefing_card",
        lambda **_kwargs: ("# Boot Briefing\n\ncompact card", []),
    )
    monkeypatch.setattr(boot_runner, "record", lambda signal, **_kw: None)
    monkeypatch.setattr(boot_runner, "write_audit_dump", lambda **_kw: None)
    monkeypatch.setattr(boot_runner, "_list_files", lambda _path: {"files": []})
    if stub_auto_inject:
        monkeypatch.setattr(
            boot_runner,
            "_resolve_web_auto_inject_skills",
            lambda *_a, **_kw: (_LARGE_INJECT_BODY, _STUB_INJECTED),
        )

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir(parents=True)
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", shared_dir)
    skills_dir = tmp_path / "boot"
    skills_dir.mkdir(parents=True)
    monkeypatch.setattr(boot_runner, "_SKILLS_INDEX_DIR", skills_dir)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(boot_audit_dump, "AUDIT_DIR", audit_dir)


def _boot_tool_result(payload: dict[str, Any]) -> ToolResult:
    """Simulate FastMCP JSON mirror + structuredContent (~2× wire form)."""
    text = json.dumps(payload, default=str)
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )


def test_build_auto_inject_skills_ref_shape() -> None:
    ref = build_auto_inject_skills_ref(_LARGE_INJECT_BODY, _STUB_INJECTED)
    assert ref["inline"] is False
    assert ref["byte_count"] == len(_LARGE_INJECT_BODY.strip().encode())
    assert len(ref["sha256"]) == 64
    assert ref["skills"] == ["orchestrator-core", "model-tier-awareness-web"]
    assert ref["delivery"] == "web_system_prompt_append"
    assert ref["ref"] == "mcp_executor._append_web_invariant_bodies"


def test_manifest_skills_hint_retains_fs_md_read_fallback() -> None:
    manifest = build_manifest(
        plan_phases=None,
        in_flight_todos=None,
        todo_total=0,
        unread_count=0,
        reflective_total=0,
        recent_mentions=None,
        skills=[{"name": "advisor-timing"}],
        agent="claude-web",
    )
    skills_row = next(row for row in manifest if row.get("section") == "skills")
    hint = skills_row["hint"]
    assert "<available_skills>" in hint
    assert 'fs(sandbox="cortex", op="md_read", path="agent-skills/<slug>.md")' in hint


def test_web_lead_boot_no_auto_inject_when_ui_attached(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _install_boot_stubs(monkeypatch, tmp_path, stub_auto_inject=False)
    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="claude-web", role="lead")

    assert "auto_inject_skills_ref" not in result
    assert "auto_inject_skills_md" not in result
    assert not any(
        a["name"] == "auto_inject_skills" for a in result["injected_artifacts"]
    )


def test_web_lead_boot_ref_only_no_inline_body(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _install_boot_stubs(monkeypatch, tmp_path)
    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="claude-web", role="lead")

    assert "auto_inject_skills_md" not in result
    ref = result["auto_inject_skills_ref"]
    assert ref["inline"] is False
    assert ref["byte_count"] > 40_000
    assert ref["skills"]
    assert ref["delivery"] == "web_system_prompt_append"

    artifact = next(
        a for a in result["injected_artifacts"] if a["name"] == "auto_inject_skills"
    )
    assert artifact["mode"] == "ref"
    assert result["byte_ledger"]["auto_inject_skills"] == ref["byte_count"]
    assert _LARGE_INJECT_BODY not in json.dumps(result)


def test_web_lead_boot_wire_size_under_headroom(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _install_boot_stubs(monkeypatch, tmp_path)
    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="claude-web", role="lead")

    wire_bytes = _measure_result(_boot_tool_result(result))
    assert wire_bytes < _WIRE_HEADROOM_BUDGET
    assert wire_bytes < _DEFAULT_THRESHOLD
    # Post de-inline structured payload stays well below pre-change ~175KB wire.
    assert wire_bytes < 80 * 1024


def test_skills_index_stays_ref_only(monkeypatch: Any, tmp_path: Path) -> None:
    _install_boot_stubs(monkeypatch, tmp_path)
    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="claude-web", role="lead")

    assert "skills_index_md" not in result
    assert result["skills_index_ref"].endswith("skills-index-claude-web.md")
    skills_artifact = next(
        a for a in result["injected_artifacts"] if a["name"] == "skills_index"
    )
    assert skills_artifact["mode"] == "written_file"
    assert skills_artifact["path"] == result["skills_index_ref"]


def test_cursor_boot_unchanged_no_auto_inject_ref(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _install_boot_stubs(monkeypatch, tmp_path, stub_auto_inject=False)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="cursor")

    assert "auto_inject_skills_ref" not in result
    assert "auto_inject_skills_md" not in result
    assert not any(
        a["name"] == "auto_inject_skills" for a in result["injected_artifacts"]
    )


@pytest.mark.asyncio
async def test_append_web_invariant_bodies_is_noop() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from services.universal_cloud_proxy.mcp_executor import _append_web_invariant_bodies

    out = await _append_web_invariant_bodies("BRIEFING_ONLY", "claude-web")
    assert out == "BRIEFING_ONLY"
