from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.cortex_named_tools import (
    _boot_audit_dump as boot_audit_dump,
    _boot_runner as boot_runner,
    _orchestration_tools as orchestration_tools,
)


class _DummyMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, title: str) -> Any:  # noqa: ARG002
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def test_boot_inspect_has_no_live_side_effects(
    monkeypatch: Any, tmp_path: Path
) -> None:
    events: list[str] = []

    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        boot_runner,
        "build_futures_spec",
        lambda _agent, _profile, _recorder: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner,
        "extract_boot_results",
        lambda _agent, _raw, _profile: {
            "sessions": [],
            "deadlines": [],
            "threads": [],
            "unread_turns": [],
            "staging_items": [],
            "todos": [],
            "self_reflections": [],
            "rj_entries": [],
            "rj_total": 0,
            "recent_mentions": [],
            "skills": [],
            "plan_phases": [],
            "in_flight_todos": [],
            "temporal_active": [],
            "expired_unresolved": [],
            "review_total": 0,
            "rag_pipeline": {},
            "audit_counters": None,
            "async_dispatches": [],
        },
    )
    monkeypatch.setattr(boot_runner, "build_unread_threads", lambda _threads: [])
    monkeypatch.setattr(boot_runner, "build_review_top", lambda _items: [])
    monkeypatch.setattr(
        boot_runner,
        "render_operational_context",
        lambda **_kwargs: "operational context for inspect",
    )
    monkeypatch.setattr(
        boot_runner,
        "render_briefing_card",
        lambda **_kwargs: (
            "briefing card 2026-05-04T02:52:19Z",
            [{"section": "Boot", "hint": "cortex(tool='entities', arguments='{}')"}],
        ),
    )
    monkeypatch.setattr(
        boot_runner, "record", lambda signal, **_kwargs: events.append(signal)
    )
    monkeypatch.setattr(
        boot_runner,
        "write_audit_dump",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("write_audit_dump should not be called in inspect mode")
        ),
    )

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir(parents=True)
    ops_file = shared_dir / "operational-context-web.md"
    ops_file.write_text("preexisting context")
    mtime_before = ops_file.stat().st_mtime_ns
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", shared_dir)

    audit_dir = tmp_path / "audit-boots"
    audit_dir.mkdir(parents=True)
    (audit_dir / "existing.md").write_text("existing")
    files_before = sorted(p.name for p in audit_dir.iterdir())
    monkeypatch.setattr(boot_audit_dump, "AUDIT_DIR", audit_dir)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["boot_inspect"](family="claude", platform="web")

    assert ops_file.stat().st_mtime_ns == mtime_before
    assert sorted(p.name for p in audit_dir.iterdir()) == files_before
    assert "mcp.cortex.boot" not in events
    assert result["mode"] == "inspect"
    assert result["briefing_card"]
    assert result["operational_context_ref"] == (
        "notes/system/shared/operational-context-claude-web.md"
    )
    assert result["audit_dump_path"] is None


def test_view_materialization_path(monkeypatch: Any, tmp_path: Path) -> None:
    """At least one fixture exercises the full view materialization path.

    Verifies that views passed to cortex_boot appear in sections_available
    with correct section keys and render_subgraph retrieval hints (§C.4).
    """
    _stub_entity_count = 4
    _stub_edge_count = 3

    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        boot_runner,
        "build_futures_spec",
        lambda _agent, _profile, _recorder: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner,
        "extract_boot_results",
        lambda _agent, _raw, _profile: {
            "sessions": [],
            "deadlines": [],
            "threads": [],
            "unread_turns": [],
            "staging_items": [],
            "todos": [],
            "self_reflections": [],
            "rj_entries": [],
            "rj_total": 0,
            "recent_mentions": [],
            "skills": [],
            "plan_phases": [],
            "in_flight_todos": [],
            "temporal_active": [],
            "expired_unresolved": [],
            "review_total": 0,
            "audit_counters": None,
            "async_dispatches": [],
        },
    )
    monkeypatch.setattr(boot_runner, "build_unread_threads", lambda _t: [])
    monkeypatch.setattr(boot_runner, "build_review_top", lambda _i: [])
    monkeypatch.setattr(
        boot_runner,
        "render_operational_context",
        lambda **_kw: "ctx",
    )
    monkeypatch.setattr(boot_runner, "record", lambda signal, **_kw: None)
    monkeypatch.setattr(boot_runner, "write_audit_dump", lambda **_kw: None)

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir(parents=True)
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", shared_dir)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(boot_audit_dump, "AUDIT_DIR", audit_dir)

    # Stub _materialize_views so no live HTTP calls are made.
    view_entity = "plan:test-roadmap"
    monkeypatch.setattr(
        boot_runner,
        "_materialize_views",
        lambda _views: [
            {
                "entity_id": view_entity,
                "entity_count": _stub_entity_count,
                "edge_count": _stub_edge_count,
                "retrieval_hint": (
                    "cortex(tool='render_subgraph', arguments='"
                    f'{{"root": "{view_entity}", "hops": 1}}\')'
                ),
            }
        ],
    )

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_boot"](
        family="claude", platform="cursor", views=[view_entity]
    )

    sections = {s["section"]: s for s in result.get("sections_available", [])}
    view_section_key = f"views/{view_entity}"
    assert view_section_key in sections, (
        f"Expected '{view_section_key}' in sections_available, got: {list(sections)}"
    )
    section = sections[view_section_key]
    assert section["entity_count"] == _stub_entity_count
    assert section["edge_count"] == _stub_edge_count
    assert "render_subgraph" in section["hint"]
    assert view_entity in result["briefing_card"]
