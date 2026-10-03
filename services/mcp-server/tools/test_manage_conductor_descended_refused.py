"""Conductor-descended manage lifecycle refuse (friction a:37762)."""

from __future__ import annotations

import pytest
from implement_admission.conductor_descent import (
    CONDUCTOR_DESCENDED_MANAGE_REASON,
    LineageView,
    descends_from_conductor,
)

from tools import manage as manage_mod

pytestmark = pytest.mark.offline


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict = {}

    def tool(self, **_kwargs):
        def decorate(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorate


def _manage_fn():
    recorder = _Recorder()
    manage_mod.register_manage_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["manage"]


def _rows(*pairs: tuple[str, LineageView]) -> dict[str, LineageView]:
    return dict(pairs)


def _patch_lookup(monkeypatch: pytest.MonkeyPatch, rows: dict[str, LineageView]) -> None:
    import implement_admission.conductor_descent as descent

    monkeypatch.setattr(
        descent, "ledger_lineage_lookup", lambda dispatch_id: rows.get(dispatch_id)
    )


def _block_sock(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    captured: list[dict] = []

    def _unexpected(body, timeout):
        captured.append(body)
        raise AssertionError("_call_manage must not run when descent refuses")

    monkeypatch.setattr(manage_mod, "_call_manage", _unexpected)
    return captured


def test_conductor_descended_sync_restart_refused_before_sock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manage = _manage_fn()
    captured = _block_sock(monkeypatch)
    _patch_lookup(
        monkeypatch,
        _rows(("nest-1", LineageView(contract="implement", nest_under="cond-1")),
              ("cond-1", LineageView(contract="conductor"))),
    )
    result = manage(
        action="sync_restart",
        service="mcp",
        caller_dispatch_id="nest-1",
    )
    assert captured == []
    assert result["reason"] == CONDUCTOR_DESCENDED_MANAGE_REASON
    assert "operator seat" in result["error"]


def test_force_true_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    manage = _manage_fn()
    _block_sock(monkeypatch)
    _patch_lookup(
        monkeypatch,
        _rows(("cond-1", LineageView(contract="conductor"))),
    )
    result = manage(
        action="sync_restart",
        service="stargate",
        force=True,
        caller_dispatch_id="cond-1",
    )
    assert result["reason"] == CONDUCTOR_DESCENDED_MANAGE_REASON


def test_nest_two_levels_under_conductor_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manage = _manage_fn()
    _block_sock(monkeypatch)
    rows = _rows(
        ("leaf", LineageView(contract="implement", nest_under="mid")),
        ("mid", LineageView(contract="freeform", nest_under="cond-1")),
        ("cond-1", LineageView(contract="conductor")),
    )
    _patch_lookup(monkeypatch, rows)
    assert descends_from_conductor("leaf", lambda i: rows.get(i)) is True
    result = manage(action="restart", service="mcp", caller_dispatch_id="leaf")
    assert result["reason"] == CONDUCTOR_DESCENDED_MANAGE_REASON


def test_non_conductor_cursor_sdk_not_refused_by_this_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manage = _manage_fn()
    captured: list[dict] = []

    def _capture(body, timeout):
        captured.append(body)
        return {"result": {"status": "ok"}}

    monkeypatch.setattr(manage_mod, "_call_manage", _capture)
    monkeypatch.setattr(manage_mod, "_extract_result", lambda raw: raw["result"])
    _patch_lookup(
        monkeypatch,
        _rows(("plain", LineageView(contract="implement"))),
    )
    result = manage(
        action="sync_restart",
        service="mcp",
        caller_dispatch_id="plain",
    )
    assert "reason" not in result or result.get("reason") != (
        CONDUCTOR_DESCENDED_MANAGE_REASON
    )
    assert captured[0]["method"] == "sync_restart"


def test_operator_seat_shape_without_dispatch_context_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cursor-sdk dispatch id. seat_class is not an input to this gate."""
    manage = _manage_fn()
    captured: list[dict] = []
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)

    def _capture(body, timeout):
        captured.append(body)
        return {"result": {"status": "ok"}}

    monkeypatch.setattr(manage_mod, "_call_manage", _capture)
    monkeypatch.setattr(manage_mod, "_extract_result", lambda raw: raw["result"])
    result = manage(action="sync_restart", service="mcp")
    assert result.get("status") == "ok"
    assert captured


def test_read_only_actions_still_forward_for_conductor_descent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manage = _manage_fn()
    captured: list[dict] = []

    def _capture(body, timeout):
        captured.append(body)
        return {"result": {"status": "ok", "action": body["method"]}}

    monkeypatch.setattr(manage_mod, "_call_manage", _capture)
    monkeypatch.setattr(manage_mod, "_extract_result", lambda raw: raw["result"])
    _patch_lookup(
        monkeypatch,
        _rows(("cond-1", LineageView(contract="conductor"))),
    )
    for action in ("status", "busy_status"):
        result = manage(action=action, caller_dispatch_id="cond-1")
        assert result["action"] == action
    assert [body["method"] for body in captured] == ["status", "busy_status"]
