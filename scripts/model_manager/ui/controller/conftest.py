"""Fail closed: grokbot egress watch tests must not touch the live desktop or edge."""

from __future__ import annotations

from typing import Any

import pytest

pytest_plugins = ["scripts.model_manager.testing.conftest"]

_REFUSAL = "unpatched grokbot egress watch reached {name}"


def _refuse(name: str):
    def _inner(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(_REFUSAL.format(name=name))

    return _inner


class GrokbotEmitRecorder:
    """Records grokbot egress watch observation emits (never touches events.sock)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self,
        signal: str,
        payload: dict[str, Any],
        **_kw: object,
    ) -> None:
        self.events.append((signal, payload))


@pytest.fixture(autouse=True)
def grokbot_egress_watch_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace live restart, edge probe, and events query with raising stubs."""
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    async def _refuse_restart(self: object) -> None:
        raise RuntimeError(_REFUSAL.format(name="_default_restart_desktop"))

    monkeypatch.setattr(
        loop_mod.GrokbotEgressWatchLoop,
        "_default_restart_desktop",
        _refuse_restart,
    )
    monkeypatch.setattr(
        loop_mod, "external_probe_hostname", _refuse("external_probe_hostname")
    )
    monkeypatch.setattr(loop_mod, "resolve_a_records", _refuse("resolve_a_records"))
    monkeypatch.setattr(loop_mod, "fetch_health", _refuse("fetch_health"))
    monkeypatch.setattr(loop_mod, "query_events_rows", _refuse("query_events_rows"))
    monkeypatch.setattr(loop_mod, "desktop_pid_alive", _refuse("desktop_pid_alive"))


@pytest.fixture(autouse=True)
def grokbot_egress_emit_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> GrokbotEmitRecorder:
    """Stub loop ``_emit`` so no test writes events.sock (a:39458)."""
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    recorder = GrokbotEmitRecorder()
    monkeypatch.setattr(loop_mod, "_emit", recorder)
    return recorder
