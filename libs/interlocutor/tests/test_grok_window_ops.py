"""Interlocutor list/spawn/retire routing for Grok displays."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from interlocutor.facade import execute_list, execute_retire, execute_spawn
from interlocutor.targets import resolve_grok_window_target, resolve_target

pytestmark = pytest.mark.offline


def test_resolve_bare_display_9() -> None:
    resolved = resolve_grok_window_target("display:9")
    assert resolved.kind == "display_grok_window"  # type: ignore[union-attr]
    assert resolved.display == "9"  # type: ignore[union-attr]


def test_resolve_display_10_grok_suffix() -> None:
    resolved = resolve_grok_window_target("display:10/grok")
    assert resolved.display == "10"  # type: ignore[union-attr]


def test_read_steer_still_require_agent_id_on_9() -> None:
    out = resolve_target("display:9")
    assert out.code == "agent_id_required"  # type: ignore[union-attr]


def test_execute_list_delegates() -> None:
    with patch("interlocutor.display_adapter.list_grok_window_sync") as list_fn:
        list_fn.return_value = {"ok": True, "agents": []}
        out = execute_list(target="display:9")
    assert out["ok"] is True
    list_fn.assert_called_once()


def test_execute_spawn_requires_resolved_window() -> None:
    with patch("interlocutor.display_adapter.spawn_grok_window_sync") as spawn_fn:
        spawn_fn.return_value = {"ok": True, "agent_id": "x"}
        out = execute_spawn(target="display:9/grok", name="tmp-bot")
    assert out["ok"] is True
    spawn_fn.assert_called_once()


def test_steer_failure_keeps_mismatch_identity() -> None:
    from interlocutor.display_adapter import _display_steer_failure

    out = _display_steer_failure(
        {
            "ok": False,
            "reason": "agent_id_mismatch",
            "active_agent_id": "live",
            "active_title": "Live",
            "titles": ["Live", "Wanted"],
        }
    )
    assert out["code"] == "target_mismatch"
    assert out["active_agent_id"] == "live"
    assert out["active_title"] == "Live"
    assert out["titles"] == ["Live", "Wanted"]


def test_execute_retire_on_display_9_agent() -> None:
    with patch("interlocutor.display_adapter.retire_grok_agent_sync") as ret:
        ret.return_value = {"ok": True}
        out = execute_retire(target="display:9/abc-agent")
    assert out["ok"] is True
    ret.assert_called_once()
