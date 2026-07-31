"""Hermetic tests for project-ask abort helpers (24911)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_bundles import project_ask_abort as paa

pytestmark = pytest.mark.offline


def _reg(*, registration_id: str = "abc123", port: int = 9234, purpose: str = "ask"):
    return SimpleNamespace(
        registration_id=registration_id,
        port=port,
        purpose=purpose,
        cdp_url=f"http://127.0.0.1:{port}",
    )


def test_purpose_kill_default_ask_only() -> None:
    assert paa.purpose_kill_default("ask") is True
    assert paa.purpose_kill_default("fable") is False
    assert paa.purpose_kill_default(None) is False


def test_registration_owns_port_active_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "active", "port": 9234}},
    )
    assert paa.registration_owns_port("abc123", 9234) is True


def test_registration_owns_port_released_or_reassigned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "released", "port": 9234}},
    )
    assert paa.registration_owns_port("abc123", 9234) is False

    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "active", "port": 9240}},
    )
    assert paa.registration_owns_port("abc123", 9234) is False


def test_deregister_on_exit_skips_when_port_reassigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "active", "port": 9240}},
    )
    monkeypatch.setattr(
        paa.cdp_registry,
        "deregister_lane",
        lambda rid, *, kill=False: calls.append((rid, kill)),
    )
    paa.deregister_on_exit(_reg(port=9234), purpose="ask")
    assert calls == []


def test_deregister_on_exit_ask_kills_when_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "active", "port": 9234}},
    )
    monkeypatch.setattr(
        paa.cdp_registry,
        "deregister_lane",
        lambda rid, *, kill=False: calls.append((rid, kill)),
    )
    paa.deregister_on_exit(_reg(), purpose="ask")
    assert calls == [("abc123", True)]


def test_deregister_on_exit_fable_no_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "active", "port": 9234}},
    )
    monkeypatch.setattr(
        paa.cdp_registry,
        "deregister_lane",
        lambda rid, *, kill=False: calls.append((rid, kill)),
    )
    paa.deregister_on_exit(_reg(purpose="fable"), purpose="fable")
    assert calls == [("abc123", False)]


def test_abort_cleanup_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    stops = {"n": 0}
    dereg: list[str] = []

    monkeypatch.setattr(
        paa.cdp_registry,
        "_load_active",
        lambda: {"abc123": {"status": "active", "port": 9234}},
    )
    monkeypatch.setattr(
        paa, "bounded_stop_via_cdp", lambda _url: stops.__setitem__("n", stops["n"] + 1)
    )
    monkeypatch.setattr(
        paa,
        "deregister_on_exit",
        lambda reg, *, purpose: dereg.append(reg.registration_id),
    )
    paa._ABORT_DONE = False
    reg = _reg()
    paa.abort_cleanup(reg, purpose="ask")
    paa.abort_cleanup(reg, purpose="ask")
    assert stops["n"] == 1
    assert dereg == ["abc123"]
