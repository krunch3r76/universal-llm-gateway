"""Regression: remote cdp-ask start must not pin fleet_deploy via SSH hang."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.model_manager.ui.controller.service_ctl import cdp_ask_remote


def _start_cmd_static_text() -> str:
    """Concatenate string parts of the ``cmd`` assignment (ignore f-expr slots)."""
    src = Path(cdp_ask_remote.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    start_fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_cdp_ask_remote"
    )
    for node in ast.walk(start_fn):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "cmd":
                parts: list[str] = []
                for chunk in ast.walk(node.value):
                    if isinstance(chunk, ast.Constant) and isinstance(chunk.value, str):
                        parts.append(chunk.value)
                return "".join(parts)
    raise AssertionError("cmd assignment not found in start_cdp_ask_remote")


def test_start_command_detaches_with_setsid_not_ampersand_and_chain() -> None:
    """Start cmd must setsid-detach; ``A && daemon &`` pins SSH / fleet_deploy."""
    cmd = _start_cmd_static_text()
    assert "setsid" in cmd
    assert "</dev/null" in cmd
    assert "nohup" not in cmd
    # Daemon line must not be chained with && before & (shell precedence trap).
    assert "&& setsid" not in cmd
    assert cmd.index("setsid") < cmd.index("&")
    # Hub Event Service TCP ingest must be exported for Jupiter followup AC-2.
    assert "EVENTS_INGEST_TCP=" in cmd


def test_run_ssh_has_bounded_timeout_constant() -> None:
    assert cdp_ask_remote._SSH_TIMEOUT_S <= 60.0
    assert cdp_ask_remote._SSH_TIMEOUT_S >= 5.0


def test_resolve_hub_events_ingest_tcp_prefers_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit EVENTS_INGEST_TCP wins over LAN default (no hard-coded hub IP)."""
    monkeypatch.setenv("EVENTS_INGEST_TCP", "9.9.9.9:7101")
    assert cdp_ask_remote.resolve_hub_events_ingest_tcp() == "9.9.9.9:7101"


def test_resolve_hub_events_ingest_tcp_host_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EVENT_SERVICE_INGEST_HOST + yaml/default port when full TCP env unset."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.setenv("EVENT_SERVICE_INGEST_HOST", "10.0.0.67")
    monkeypatch.delenv("EVENTS_INGEST_PORT", raising=False)
    monkeypatch.setattr(
        cdp_ask_remote,
        "load_event_service_config",
        lambda: type("C", (), {"tcp_ingest_port": 7101})(),
    )
    assert cdp_ask_remote.resolve_hub_events_ingest_tcp() == "10.0.0.67:7101"
