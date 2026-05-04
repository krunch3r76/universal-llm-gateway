from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.cortex_named_tools import (
    _boot_audit_dump,
    _boot_runner,
    _orchestration_tools,
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

    monkeypatch.setattr(_boot_runner, "_resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        _boot_runner,
        "_build_futures_spec",
        lambda _agent, _profile, _recorder: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        _boot_runner,
        "_extract_boot_results",
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
        },
    )
    monkeypatch.setattr(_boot_runner, "_build_unread_threads", lambda _threads: [])
    monkeypatch.setattr(_boot_runner, "_build_review_top", lambda _items: [])
    monkeypatch.setattr(
        _boot_runner,
        "render_operational_context",
        lambda **_kwargs: "operational context for inspect",
    )
    monkeypatch.setattr(
        _boot_runner,
        "render_briefing_card",
        lambda **_kwargs: (
            "briefing card 2026-05-04T02:52:19Z",
            [{"section": "Boot", "hint": "cortex(tool='entities', arguments='{}')"}],
        ),
    )
    monkeypatch.setattr(
        _boot_runner, "record", lambda signal, **_kwargs: events.append(signal)
    )
    monkeypatch.setattr(
        _boot_runner,
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
    monkeypatch.setattr(_boot_runner, "_OPS_CONTEXT_DIR", shared_dir)

    audit_dir = tmp_path / "audit-boots"
    audit_dir.mkdir(parents=True)
    (audit_dir / "existing.md").write_text("existing")
    files_before = sorted(p.name for p in audit_dir.iterdir())
    monkeypatch.setattr(_boot_audit_dump, "AUDIT_DIR", audit_dir)

    mcp = _DummyMcp()
    _orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["boot_inspect"](agent="web")

    assert ops_file.stat().st_mtime_ns == mtime_before
    assert sorted(p.name for p in audit_dir.iterdir()) == files_before
    assert "mcp.cortex.boot" not in events
    assert result["mode"] == "inspect"
    assert result["briefing_card"]
    # Inline emission retired — INSPECT no longer ships ~22 KB of
    # operational_context prose; callers `fs read` the path directly, the
    # same way LIVE consumers always have. The path is reported regardless
    # of whether THIS boot wrote the file (LIVE writes; INSPECT defers).
    assert result["operational_context_inline"] is None
    assert result["operational_context_ref"] == (
        "notes/system/shared/operational-context-web.md"
    )
    assert result["audit_dump_path"] is None
