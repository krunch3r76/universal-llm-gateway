"""Reaping the navigator lease releases on a replied execution, never on doubt.

The bug these pin (a:33724): ``release_navigator_lease`` was only reachable
from the submit-failure branch, so a successful navigator wake held the lease
for the full 6300s TTL and the cheap cdp tier went dark for up to 105 minutes
at a time. The reaper must close that loop **without** ever releasing a lease
whose execution might still be streaming — two navigators on one house is a
worse failure than a slow one.
"""

from __future__ import annotations

from typing import Any

import pytest

from bus_watch import digest_budget, navigator_lease_reap, navigator_wake

_LIVE_LOCK = {
    "holder": "liaison-ticker",
    "execution_id": "338316c6-aacc-44a9-a08c-f9d48e1cfca2",
    "claimed_at": "2026-09-14T13:29:48Z",
}


@pytest.fixture
def released(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Spy on releases so a test can assert the lease was *not* dropped."""
    calls: list[str] = []

    def _release(root_id: str, **_: Any) -> dict[str, Any]:
        calls.append(root_id)
        return {"ok": True}

    monkeypatch.setattr(navigator_wake, "release_navigator_lease", _release)
    return calls


def _lock(monkeypatch: pytest.MonkeyPatch, lock: dict[str, Any]) -> None:
    monkeypatch.setattr(navigator_wake, "read_navigator_lock", lambda _r: dict(lock))
    monkeypatch.setattr(navigator_wake, "_lease_expired", lambda _l: False)


def _turns(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """Stub the bus read; ``payload`` is whatever ``_get`` would return."""

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(digest_budget, "_bus", lambda: _Client())
    monkeypatch.setattr(digest_budget, "_get", lambda *_a, **_k: payload)


def test_reaps_when_the_execution_has_replied(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    _lock(monkeypatch, _LIVE_LOCK)
    _turns(monkeypatch, {"turns": [{"subject": "cdp reply — 338316c6"}]})
    out = navigator_lease_reap.reap_navigator_lease("10479")
    assert out["reaped"] is True, out
    assert out["reason"] == "execution_replied"
    assert released == ["10479"]


def test_holds_while_the_execution_is_still_running(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    """The decisive case: a scan that found nothing must never release."""
    _lock(monkeypatch, _LIVE_LOCK)
    _turns(monkeypatch, {"turns": [{"subject": "DIGEST 10479 2026-09-14T14:00:00Z"}]})
    out = navigator_lease_reap.reap_navigator_lease("10479")
    assert out["reaped"] is False
    assert out["reason"] == "execution_in_flight"
    assert released == []


def test_unreadable_bus_holds_the_lease(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    _lock(monkeypatch, _LIVE_LOCK)
    _turns(monkeypatch, {"_error": "http_503"})
    out = navigator_lease_reap.reap_navigator_lease("10479")
    assert out["reaped"] is False
    assert out["reason"] == "bus_unreadable"
    assert released == []


def test_bus_exception_holds_the_lease(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    _lock(monkeypatch, _LIVE_LOCK)

    def _boom() -> None:
        raise OSError("socket gone")

    monkeypatch.setattr(digest_budget, "_bus", _boom)
    out = navigator_lease_reap.reap_navigator_lease("10479")
    assert out["reaped"] is False
    assert out["reason"] == "bus_unreadable"
    assert released == []


def test_lease_without_execution_id_is_left_to_ttl(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    _lock(monkeypatch, {"holder": "liaison-ticker"})
    _turns(monkeypatch, {"turns": [{"subject": "cdp reply — 338316c6"}]})
    out = navigator_lease_reap.reap_navigator_lease("10479")
    assert out["reaped"] is False
    assert out["reason"] == "no_execution_id"
    assert released == []


def test_free_and_expired_leases_are_not_reaped(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    monkeypatch.setattr(navigator_wake, "read_navigator_lock", lambda _r: {})
    assert navigator_lease_reap.reap_navigator_lease("10479")["reason"] == "no_holder"

    monkeypatch.setattr(
        navigator_wake, "read_navigator_lock", lambda _r: dict(_LIVE_LOCK)
    )
    monkeypatch.setattr(navigator_wake, "_lease_expired", lambda _l: True)
    out = navigator_lease_reap.reap_navigator_lease("10479")
    assert out["reason"] == "ttl_already_expired"
    assert released == []


def test_dry_run_reports_without_releasing(
    monkeypatch: pytest.MonkeyPatch, released: list[str]
) -> None:
    _lock(monkeypatch, _LIVE_LOCK)
    _turns(monkeypatch, {"turns": [{"subject": "cdp reply — 338316c6"}]})
    out = navigator_lease_reap.reap_navigator_lease("10479", dry_run=True)
    assert out["reaped"] is False
    assert out["would_reap"] is True
    assert released == []


@pytest.mark.parametrize("ident", ["", "  ", "338316"])
def test_unusable_execution_id_is_unknown_not_absent(
    monkeypatch: pytest.MonkeyPatch, ident: str
) -> None:
    """Too-short ids return ``None`` (unknown), never ``False`` (observed)."""
    _turns(monkeypatch, {"turns": [{"subject": "cdp reply — 338316c6"}]})
    assert navigator_lease_reap.execution_reply_landed("10479", ident) is None


def test_malformed_payload_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _turns(monkeypatch, {"turns": "not-a-list"})
    assert (
        navigator_lease_reap.execution_reply_landed("10479", _LIVE_LOCK["execution_id"])
        is None
    )
