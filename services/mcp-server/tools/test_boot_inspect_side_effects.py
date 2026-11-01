from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_extract_stub import stub_extract_boot_results
from tools.cortex_named_tools import (
    _boot_audit_dump as boot_audit_dump,
)
from tools.cortex_named_tools import (
    _boot_data_fetch as data_fetch,
)
from tools.cortex_named_tools import (
    _boot_runner as boot_runner,
)
from tools.cortex_named_tools import (
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
        lambda _agent, _profile, _recorder, **_kw: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner,
        "extract_boot_results",
        stub_extract_boot_results,
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
    result = mcp.tools["boot_inspect"](seat="claude-web")

    assert ops_file.stat().st_mtime_ns == mtime_before
    assert sorted(p.name for p in audit_dir.iterdir()) == files_before
    assert "mcp.cortex.boot" not in events
    assert result["mode"] == "inspect"
    assert result["briefing_card"]
    assert result["operational_context_ref"] == (
        "notes/system/shared/operational-context-claude-web.md"
    )
    assert result["audit_dump_path"] is None


def test_boot_operational_context_ref_ignores_role_suffix(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """role= must not advertise a -role-{role} path that fs cannot resolve."""
    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        boot_runner,
        "build_futures_spec",
        lambda _agent, _profile, _recorder, **_kw: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner,
        "extract_boot_results",
        stub_extract_boot_results,
    )
    monkeypatch.setattr(boot_runner, "build_unread_threads", lambda _threads: [])
    monkeypatch.setattr(boot_runner, "build_review_top", lambda _items: [])
    monkeypatch.setattr(
        boot_runner,
        "render_operational_context",
        lambda **_kwargs: "operational context for lead boot",
    )
    monkeypatch.setattr(
        boot_runner,
        "render_briefing_card",
        lambda **_kwargs: ("briefing card", []),
    )
    monkeypatch.setattr(boot_runner, "record", lambda signal, **_kw: None)
    monkeypatch.setattr(boot_runner, "write_audit_dump", lambda **_kw: None)

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir(parents=True)
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", shared_dir)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(boot_audit_dump, "AUDIT_DIR", audit_dir)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="claude-web", role="lead")

    seat_path = "notes/system/shared/operational-context-claude-web.md"
    assert result["operational_context_ref"] == seat_path
    op_ctx = next(
        a for a in result["injected_artifacts"] if a["name"] == "operational_context"
    )
    assert op_ctx["path"] == seat_path
    assert (shared_dir / "operational-context-claude-web.md").is_file()


def test_view_materialization_path(monkeypatch: Any, tmp_path: Path) -> None:
    """At least one fixture exercises the full view materialization path.

    Verifies that views passed to cortex_brief appear in sections_available
    with correct section keys and render_subgraph retrieval hints (§C.4).
    """
    _stub_entity_count = 4
    _stub_edge_count = 3

    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        boot_runner,
        "build_futures_spec",
        lambda _agent, _profile, _recorder, **_kw: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner,
        "extract_boot_results",
        stub_extract_boot_results,
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
    result = mcp.tools["cortex_brief"](seat="cursor", views=[view_entity])

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


def _logical_registry(tmp_path: Path, monkeypatch: Any) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    aliases: [alpha]
    family: gpt
    platform: web
    inbox_recipient: alpha-inbox
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))


def _isolate_boot_io(
    monkeypatch: Any, tmp_path: Path, events: list[tuple[str, dict]]
) -> None:
    def _capture(signal: str, **kwargs: Any) -> None:
        events.append((signal, kwargs))

    def _cx(method: str, path: str, *_a: Any, **_k: Any) -> dict:
        assert method == "GET"
        if path.startswith("/skills"):
            return {
                "items": [{"id": "agent_skill:demo"}],
                "rendered": {"concise_markdown": "# skills index\n"},
                "unpartitioned_count": 0,
            }
        if path.startswith("/threads") or path.startswith("/turns/"):
            return {
                "threads": [],
                "total_unread_threads": 0,
                "total_unread_turns": 0,
                "active_since": "14d",
            }
        if path.startswith("/boot-continuity"):
            return {
                "last_session": None,
                "continuity_chain": [],
                "continuations": [],
                "hints": [],
            }
        return {"items": []}

    def _relay(_sandbox: str, method: str, path: str, *_a: Any, **_k: Any) -> dict:
        return _cx(method, path)

    monkeypatch.setattr(data_fetch, "cortex_cx", _cx)
    monkeypatch.setattr(data_fetch, "_relay", _relay)
    monkeypatch.setattr(
        data_fetch, "_fetch_async_dispatches_from_events", lambda _agent: []
    )
    monkeypatch.setattr(boot_runner, "_list_files", lambda _prefix: {"files": []})
    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(boot_runner, "record", _capture)
    monkeypatch.setattr(boot_audit_dump, "record", _capture)
    ops = tmp_path / "ops"
    skills = tmp_path / "skills"
    audit = tmp_path / "audit"
    ops.mkdir()
    skills.mkdir()
    audit.mkdir()
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", ops)
    monkeypatch.setattr(boot_runner, "_SKILLS_INDEX_DIR", skills)
    monkeypatch.setattr(boot_audit_dump, "AUDIT_DIR", audit)


def test_inspect_ceiling_plus_one_emits_warning_without_events(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Breaks when an over-ceiling INSPECT card writes events or omits the warning."""
    events: list[tuple[str, dict]] = []
    _isolate_boot_io(monkeypatch, tmp_path, events)
    _logical_registry(tmp_path, monkeypatch)

    def _over_ceiling(**kwargs: Any) -> tuple[str, list]:
        ceiling = boot_runner._card_ceiling_for(kwargs["agent"], kwargs.get("domain"))
        return ("x" * (ceiling + 1), [])

    monkeypatch.setattr(boot_runner, "render_briefing_card", _over_ceiling)
    writes: list[str] = []

    def _no_write(*_a: Any, **_k: Any) -> None:
        writes.append("durable_write_text")
        raise AssertionError("INSPECT must not write operational or skills files")

    monkeypatch.setattr(boot_runner, "durable_write_text", _no_write)
    monkeypatch.setattr(
        boot_runner,
        "write_audit_dump",
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("INSPECT must not write an audit dump")
        ),
    )

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    cursor = mcp.tools["boot_inspect"](seat="cursor")
    logical = mcp.tools["boot_inspect"](seat="alpha-work")
    for result in (cursor, logical):
        assert "Card over budget" in result["briefing_card"]
        assert result["mode"] == "inspect"
        assert result["audit_dump_path"] is None
    assert events == []
    assert writes == []


def test_live_overbudget_event_and_skills_index_failure_attribution(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Breaks when LIVE drops the overbudget event or attributes a skills-index failure to the capability slug."""
    events: list[tuple[str, dict]] = []
    _isolate_boot_io(monkeypatch, tmp_path, events)
    _logical_registry(tmp_path, monkeypatch)

    def _over_ceiling(**kwargs: Any) -> tuple[str, list]:
        ceiling = boot_runner._card_ceiling_for(kwargs["agent"], kwargs.get("domain"))
        return ("x" * (ceiling + 1), [])

    monkeypatch.setattr(boot_runner, "render_briefing_card", _over_ceiling)

    def _disk_full(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(boot_runner, "durable_write_text", _disk_full)
    monkeypatch.setattr(boot_runner, "write_audit_dump", lambda **_k: None)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="alpha-work")
    assert "Card over budget" in result["briefing_card"]
    overbudget = [
        kw for signal, kw in events if signal == "mcp.cortex.boot.card.overbudget"
    ]
    assert overbudget
    assert overbudget[0]["agent"] == "alpha-work"
    failed = [
        kw
        for signal, kw in events
        if signal == "mcp.cortex.boot.skills_index.writefailed"
    ]
    assert failed
    assert failed[0]["agent"] == "alpha-work"
    assert failed[0]["agent"] != "gpt-web"


def _fake_brief(**kwargs: Any) -> dict[str, Any]:
    identity = kwargs["identity"]
    return {
        "mode": kwargs["mode"].value,
        "briefing_card": f"card:{identity.agent}",
        "sections_available": [],
        "injected_artifacts": [],
        "session_identity": {"session_id": "inspect", "agent": identity.agent},
        "audit_dump_path": None,
    }


def test_boot_inspect_diff_with_matrix(monkeypatch: Any, tmp_path: Path) -> None:
    """Breaks when diff_with falls through to a default seat or raises on unknown hyphenated text."""
    calls: list[str] = []

    def _run(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["identity"].agent)
        return _fake_brief(**kwargs)

    monkeypatch.setattr(orchestration_tools, "run_cortex_brief", _run)
    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    inspect = mcp.tools["boot_inspect"]

    unknown_plain = inspect(seat="cursor", diff_with="unregistered")
    assert unknown_plain["diff"] is None
    assert unknown_plain["secondary"]["reason"] == "seat_unresolved"
    assert calls == ["claude-cursor"]
    assert "claude-cursor" not in str(unknown_plain["secondary"])

    calls.clear()
    unknown_hyphen = inspect(seat="cursor", diff_with="no-such-seat")
    assert unknown_hyphen["diff"] is None
    assert unknown_hyphen["secondary"]["reason"] == "seat_unresolved"
    assert calls == ["claude-cursor"]

    _logical_registry(tmp_path, monkeypatch)
    calls.clear()
    logical = inspect(seat="cursor", diff_with="alpha")
    assert calls == ["claude-cursor", "alpha-work"]
    assert logical["diff"] is not None
    assert logical["secondary"]["session_identity"]["agent"] == "alpha-work"
    assert logical["secondary"]["briefing_card"] == "card:alpha-work"

    bad = tmp_path / "bad-seats.yaml"
    bad.write_text("schema_version: 9\nseats: {}\n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(bad))
    calls.clear()
    malformed = inspect(seat="cursor", diff_with="alpha-work")
    assert malformed["diff"] is None
    assert malformed["secondary"]["reason"] == "unsupported_schema_version"
    assert calls == ["claude-cursor"]
