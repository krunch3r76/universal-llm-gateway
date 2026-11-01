"""Logical-seat boot fetch wiring — agent vs capability separation."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from agent_seat.boot_identity import (
    BootIdentity,
    legacy_boot_identity,
    resolve_boot_identity_for_seat,
)

from tools._boot_helpers import _briefing_card as card_mod
from tools.cortex_named_tools import _boot_data_fetch as data_fetch
from tools.cortex_named_tools import _boot_runner as boot_runner
from tools.cortex_named_tools._boot_data_fetch import build_futures_spec
from tools.cortex_named_tools._boot_runner import (
    BootMode,
    _resolve_last_session,
    run_cortex_brief,
)


class _Recorder:
    def wrap(self, _name: str, fn: Any) -> Any:
        return fn


def test_build_futures_spec_logical_inbox_and_filters() -> None:
    logical = legacy_boot_identity("gpt", "web")
    logical = logical.__class__(
        family=logical.family,
        platform=logical.platform,
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-inbox",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
        house="9999",
    )
    profile = {
        "session_limit": 3,
        "include_deadlines": True,
        "include_review_queue": True,
    }
    spec = build_futures_spec(
        logical.capability_slug,
        profile,
        _Recorder(),
        identity=logical,
    )
    unread_path = spec["unread_toc"][-1]
    assert "to=alpha-inbox" in unread_path
    session_path = spec["sessions"][-1]
    assert "agent=alpha-work" in session_path
    skills_path = spec["skills"][-1]
    assert "for_agent=gpt-web" in skills_path


def test_build_futures_spec_uses_identity_self_entity_id() -> None:
    logical = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
    )
    profile = {"self_reflections_limit": 3}
    spec = build_futures_spec("gpt-web", profile, _Recorder(), identity=logical)
    assert "self_reflections" in spec
    assert "entity_id=seat%3Aalpha-work" in spec["self_reflections"][-1]


def test_continuity_for_briefing_card_strips_foreign_chain() -> None:
    from tools.cortex_named_tools._boot_runner import _continuity_for_briefing_card

    logical = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
    )
    extracted = {
        "continuity": {
            "continuity_chain": [
                "other-seat-2026-01-01-010101-abc",
                "alpha-work-2026-01-02-010101-def",
            ],
            "error": None,
        }
    }
    out = _continuity_for_briefing_card(logical, extracted)
    assert out is not None
    assert out["continuity_chain"] == ["alpha-work-2026-01-02-010101-def"]


def test_resolve_last_session_rejects_foreign_continuity() -> None:
    logical = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
    )
    extracted = {
        "continuity": {"last_session": {"agent": "other-seat", "summary": "x"}},
        "sessions": [{"agent": "other-seat", "summary": "y"}],
    }
    assert _resolve_last_session(logical, extracted) is None


_REGISTRY = """
schema_version: 1
seats:
  alpha-work:
    aliases: [alpha]
    family: gpt
    platform: web
    house: "9999"
    surface: chatgpt
    inbox_recipient: alpha-inbox
  beta-web:
    aliases: [beta]
    family: gpt
    platform: web
    house: "9998"
    surface: chatgpt
    inbox_recipient: beta-inbox
"""

# Pinned from base 39eebcc build_futures_spec / build_manifest, not from this candidate.
_BASE_LEGACY_SESSIONS = "cortex GET /session-journals?limit=3"
_BASE_SESSIONS_HINT = "cortex(tool='journal_read', arguments='{\"limit\": 5}')"
_BASE_CONTINUITY_HINT = "GET /boot-continuity via cortex-api"
_BASE_SELF_HINT = (
    "cortex(tool='assertions', arguments='{\"entity_id\": \"ai_agent:AGENT\"}')"
)
_FOREIGN = "beta-work-2026-06-02-010101-bbb"
_ALPHA_SID = "alpha-work-2026-06-01-010101-aaa"


def _write_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(_REGISTRY.strip() + "\n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))


def _empty_payload(path: str) -> dict[str, Any]:
    if path.startswith("/boot-continuity"):
        return {
            "last_session": None,
            "continuity_chain": [],
            "continuations": [],
            "hints": [],
        }
    if path.startswith("/threads") or path.startswith("/turns/"):
        return {
            "threads": [],
            "total_unread_threads": 0,
            "total_unread_turns": 0,
            "active_since": "14d",
        }
    return {"items": []}


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cx: Any | None = None,
    transcript: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stub external fetches. The runner, recorder, and card renderer stay real."""
    captured: dict[str, Any] = {"orientation": [], "async_agents": []}
    real_orientation = card_mod.render_orientation_blocks

    def _spy_orientation(**kwargs: Any) -> list[str]:
        captured["orientation"].append(dict(kwargs))
        return real_orientation(**kwargs)

    def _cx(method: str, path: str, *_args: Any, **_kwargs: Any) -> Any:
        assert method == "GET"
        if cx is not None:
            return cx(method, path)
        return _empty_payload(path)

    def _relay(_sandbox: str, method: str, path: str, *_a: Any, **_k: Any) -> Any:
        assert method == "GET"
        return _empty_payload(path)

    def _async(agent: str) -> list[dict[str, Any]]:
        captured["async_agents"].append(agent)
        return []

    monkeypatch.setattr(data_fetch, "cortex_cx", _cx)
    monkeypatch.setattr(data_fetch, "_relay", _relay)
    monkeypatch.setattr(data_fetch, "_fetch_async_dispatches_from_events", _async)
    monkeypatch.setattr(boot_runner, "_list_files", lambda _prefix: {"files": []})
    monkeypatch.setattr(boot_runner, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: transcript)
    monkeypatch.setattr(card_mod, "render_orientation_blocks", _spy_orientation)
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", tmp_path / "ops")
    monkeypatch.setattr(boot_runner, "_SKILLS_INDEX_DIR", tmp_path / "skills")
    return captured


def _tools(result: dict[str, Any]) -> list[str]:
    card = next(a for a in result["injected_artifacts"] if a["name"] == "briefing_card")
    return [row["tool"] for row in card["fetches"]]


def _hint(result: dict[str, Any], section: str) -> str:
    return next(
        row["hint"] for row in result["sections_available"] if row["section"] == section
    )


def _identity(seat: str) -> BootIdentity:
    resolved = resolve_boot_identity_for_seat(seat)
    assert isinstance(resolved, BootIdentity)
    return resolved


def test_runner_fetch_graph_legacy_and_distinct_logical_seats(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when logical seats borrow policy from their name, or legacy queries drift from base."""
    _write_registry(tmp_path, monkeypatch)
    captured = _patch_runner(monkeypatch, tmp_path)
    seats = ("cursor", "claude-web", "gpt-web", "alpha-work", "beta-web")
    results = {
        seat: run_cortex_brief(identity=_identity(seat), mode=BootMode.INSPECT)
        for seat in seats
    }
    assert captured["async_agents"] == [
        "claude-cursor",
        "claude-web",
        "gpt-web",
        "alpha-work",
        "beta-web",
    ]
    assert [row["agent"] for row in captured["orientation"]] == [
        "claude-cursor",
        "claude-web",
        "gpt-web",
        "gpt-web",
        "gpt-web",
    ]
    assert captured["orientation"][3] == captured["orientation"][4]

    cursor_tools = _tools(results["cursor"])
    web_tools = _tools(results["claude-web"])
    assert _BASE_LEGACY_SESSIONS in cursor_tools
    assert _BASE_LEGACY_SESSIONS in web_tools
    assert all(
        "agent=" not in tool for tool in cursor_tools if "session-journals" in tool
    )
    assert "cortex GET /boot-continuity?agent=claude-cursor" in cursor_tools
    assert "cortex GET /boot-continuity?agent=claude-web" in web_tools
    assert (
        "cortex GET /skills?limit=120&for_agent=claude-cursor&view=boot&render=concise%2Ccard"
        in cursor_tools
    )
    assert (
        "cortex GET /skills?limit=120&for_agent=claude-web&view=boot&render=concise%2Ccard"
        in web_tools
    )
    assert (
        "agent-bus GET /turns/unread-toc?to=claude-cursor&active_since=14d&limit=10"
        in cursor_tools
    )
    assert "## Identity" not in results["cursor"]["briefing_card"]
    assert "## Identity" not in results["claude-web"]["briefing_card"]
    assert _hint(results["cursor"], "sessions") == _BASE_SESSIONS_HINT
    assert _hint(results["cursor"], "continuity") == _BASE_CONTINUITY_HINT
    assert _hint(results["cursor"], "self_reflections") == _BASE_SELF_HINT

    alpha_tools = _tools(results["alpha-work"])
    beta_tools = _tools(results["beta-web"])
    gpt_tools = _tools(results["gpt-web"])
    alpha_todos = next(tool for tool in alpha_tools if "/boot-todos?" in tool)
    beta_todos = next(tool for tool in beta_tools if "/boot-todos?" in tool)
    gpt_todos = next(tool for tool in gpt_tools if "/boot-todos?" in tool)
    assert alpha_todos == beta_todos == gpt_todos
    assert "alpha-work" not in alpha_todos
    assert "beta-web" not in beta_todos
    for tools in (alpha_tools, beta_tools, gpt_tools):
        assert (
            "cortex GET /skills?limit=120&for_agent=gpt-web&view=boot&render=concise%2Ccard"
            in tools
        )
    assert "cortex GET /session-journals?limit=3&agent=alpha-work" in alpha_tools
    assert "cortex GET /session-journals?limit=3&agent=beta-web" in beta_tools
    assert (
        "agent-bus GET /turns/unread-toc?to=alpha-inbox&active_since=14d&limit=10"
        in alpha_tools
    )
    assert (
        "agent-bus GET /turns/unread-toc?to=beta-inbox&active_since=14d&limit=10"
        in beta_tools
    )
    assert "cortex GET /boot-reflective?agent=alpha-work&limit=5" in alpha_tools
    assert "cortex GET /boot-reflective?agent=beta-web&limit=5" in beta_tools
    assert "entity_id=seat%3Aalpha-work" in " ".join(alpha_tools)
    assert "entity_id=seat%3Abeta-web" in " ".join(beta_tools)
    assert "entity_id=family%3Agpt" in " ".join(gpt_tools)
    assert "seat:alpha-work" in results["alpha-work"]["briefing_card"]
    assert "alpha-inbox" in results["alpha-work"]["briefing_card"]
    assert "alpha-inbox" not in results["beta-web"]["briefing_card"]
    assert "beta-inbox" not in results["alpha-work"]["briefing_card"]
    assert results["alpha-work"]["session_identity"]["agent"] == "alpha-work"
    assert results["beta-web"]["session_identity"]["agent"] == "beta-web"
    assert _hint(results["alpha-work"], "sessions") == (
        "GET /session-journals?agent=alpha-work via cortex-api"
    )
    assert _hint(results["alpha-work"], "continuity") == (
        "GET /boot-continuity?agent=alpha-work via cortex-api"
    )
    assert "seat:alpha-work" in _hint(results["alpha-work"], "self_reflections")


def _run_logical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cx: Any,
    transcript: dict[str, Any] | None = None,
    transcript_id: str = "",
) -> dict[str, Any]:
    _write_registry(tmp_path, monkeypatch)
    _patch_runner(monkeypatch, tmp_path, cx=cx, transcript=transcript)
    return run_cortex_brief(
        identity=_identity("alpha-work"),
        mode=BootMode.INSPECT,
        transcript_id=transcript_id,
    )


def test_runner_matching_continuity_reaches_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a matching logical last-session is dropped before render."""

    def cx(_method: str, path: str) -> Any:
        if path.startswith("/boot-continuity"):
            return {
                "last_session": {
                    "agent": "alpha-work",
                    "session_id": _ALPHA_SID,
                    "summary": "alpha-owned-summary",
                    "timestamp": "2026-06-01T00:00:00Z",
                },
                "continuity_chain": [_ALPHA_SID],
                "continuations": [],
                "hints": [],
            }
        return _empty_payload(path)

    result = _run_logical(monkeypatch, tmp_path, cx=cx)
    card = result["briefing_card"]
    assert _ALPHA_SID in card
    assert "alpha-owned-summary" in card
    assert result["session_identity"]["agent"] == "alpha-work"


def test_runner_absent_and_malformed_continuity_render_without_foreign_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a missing or non-dict continuity payload raises or invents a session."""

    def absent(_method: str, path: str) -> Any:
        if path.startswith("/boot-continuity"):
            return {
                "last_session": None,
                "continuity_chain": [],
                "continuations": [],
                "hints": [],
            }
        return _empty_payload(path)

    absent_card = _run_logical(monkeypatch, tmp_path, cx=absent)["briefing_card"]
    assert _ALPHA_SID not in absent_card
    assert _FOREIGN not in absent_card

    def malformed(_method: str, path: str) -> Any:
        if path.startswith("/boot-continuity"):
            return ["not-a-mapping"]  # type: ignore[return-value]
        return _empty_payload(path)

    malformed_result = _run_logical(monkeypatch, tmp_path, cx=malformed)
    assert malformed_result["session_identity"]["agent"] == "alpha-work"
    assert _FOREIGN not in malformed_result["briefing_card"]


def test_runner_error_continuity_uses_same_agent_journal_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when an error payload prints a foreign chain or skips the same-agent journal."""

    def cx(_method: str, path: str) -> Any:
        if path.startswith("/boot-continuity"):
            return {
                "error": "continuity_down",
                "last_session": {
                    "agent": "beta-work",
                    "session_id": _FOREIGN,
                    "summary": "foreign-summary",
                },
                "continuity_chain": [_FOREIGN],
            }
        if path.startswith("/session-journals"):
            return {
                "items": [
                    {
                        "agent": "beta-work",
                        "session_id": _FOREIGN,
                        "summary": "foreign-summary",
                    },
                    {
                        "agent": "alpha-work",
                        "session_id": _ALPHA_SID,
                        "summary": "alpha-fallback-summary",
                        "timestamp": "2026-06-01T00:00:00Z",
                    },
                ]
            }
        return _empty_payload(path)

    card = _run_logical(monkeypatch, tmp_path, cx=cx)["briefing_card"]
    assert "alpha-fallback-summary" in card
    assert "last session (alpha-work" in card
    assert _FOREIGN not in card
    assert "foreign-summary" not in card


def test_runner_rejected_foreign_chain_never_reaches_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a mismatched continuity chain is still printed on the logical card."""

    def cx(_method: str, path: str) -> Any:
        if path.startswith("/boot-continuity"):
            return {
                "last_session": {
                    "agent": "beta-work",
                    "session_id": _FOREIGN,
                    "summary": "foreign-summary",
                },
                "continuity_chain": [_FOREIGN, "other-seat-2026-01-01-010101-abc"],
                "continuations": [],
                "hints": [],
            }
        if path.startswith("/session-journals"):
            return {"items": []}
        return _empty_payload(path)

    card = _run_logical(monkeypatch, tmp_path, cx=cx)["briefing_card"]
    assert _FOREIGN not in card
    assert "foreign-summary" not in card
    assert "other-seat-2026-01-01-010101-abc" not in card


def test_foreign_transcript_does_not_retarget_session_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when an attached foreign transcript replaces the logical session identity."""

    def cx(_method: str, path: str) -> Any:
        if path.startswith("/boot-continuity"):
            return {
                "last_session": {
                    "agent": "alpha-work",
                    "session_id": _ALPHA_SID,
                    "summary": "alpha-owned-summary",
                    "timestamp": "2026-06-01T00:00:00Z",
                },
                "continuity_chain": [_ALPHA_SID],
                "continuations": [],
                "hints": [],
            }
        return _empty_payload(path)

    result = _run_logical(
        monkeypatch,
        tmp_path,
        cx=cx,
        transcript_id=_FOREIGN,
        transcript={
            "entity_id": f"transcript:{_FOREIGN}",
            "transcript_id": _FOREIGN,
            "description": "attached-foreign-transcript",
            "assertions": [],
        },
    )
    assert result["session_identity"]["agent"] == "alpha-work"
    assert f"transcript:{_FOREIGN}" in result["briefing_card"]
    assert "alpha-owned-summary" in result["briefing_card"]
    assert "last session (beta-work" not in result["briefing_card"]


_BOOT_IDENTITY_IMPORTERS = {
    "libs/agent_seat/boot_identity.py",
    "services/mcp-server/tools/cortex_named_tools/_boot_seat_resolve.py",
    "services/mcp-server/tools/cortex_named_tools/_orchestration_tools.py",
    "services/mcp-server/tools/cortex_named_tools/_boot_runner.py",
    "services/mcp-server/tools/cortex_named_tools/_boot_data_fetch.py",
    "services/mcp-server/tools/_boot_helpers/_briefing_card.py",
    "services/mcp-server/tools/_boot_helpers/_manifest.py",
}


def _imports_boot_identity(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "agent_seat.boot_identity":
                return True
            if module == "agent_seat" and any(
                alias.name == "boot_identity" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agent_seat.boot_identity" or alias.name.startswith(
                    "agent_seat.boot_identity."
                ):
                    return True
    return False


def test_boot_identity_import_boundary_excludes_binding() -> None:
    """Breaks when binding or another non-boot module starts reading the private registry."""
    repo = Path(__file__).resolve().parents[3]
    offenders: list[str] = []
    for root in (repo / "libs", repo / "services"):
        for path in root.rglob("*.py"):
            relative = path.relative_to(repo).as_posix()
            if "/tests/" in f"/{relative}" or path.name.startswith("test_"):
                continue
            if any(part in {".git", "__pycache__", "tmp"} for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if (
                _imports_boot_identity(tree)
                and relative not in _BOOT_IDENTITY_IMPORTERS
            ):
                offenders.append(relative)
    assert offenders == []


def test_wake_binding_unchanged_across_registry_states(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when private boot-registry contents change a wake delivery binding."""
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("CORTEX_BOOT_IDENTITIES_PATH", raising=False)
    from agent_bus_store.db import init_db
    from agent_bus_store.db.endpoint_wake_ledger import resolve_wake_delivery_binding

    init_db()

    def snap() -> Any:
        payload = dict(resolve_wake_delivery_binding(house_id="21207"))
        payload.pop("as_of", None)
        return payload

    baseline = snap()
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(_REGISTRY.strip() + "\n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))
    assert snap() == baseline
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    house: "1111"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    assert snap() == baseline
    cfg.write_text("schema_version: 9\nseats: {}\n", encoding="utf-8")
    assert snap() == baseline
