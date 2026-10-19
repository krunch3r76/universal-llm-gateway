"""Roster promotion — fork 10 (T11–T14)."""

from __future__ import annotations

import importlib
from typing import Any
from urllib.parse import urlparse

import pytest

from bus_watch.roster import append_row, merge_by_row_id, read_journal, roster_path
from bus_watch.roster_promote import maybe_promote_roster


@pytest.fixture()
def watch_dir(tmp_path, monkeypatch):  # noqa: ANN001
    d = tmp_path / "watchers"
    d.mkdir()
    monkeypatch.setattr("bus_watch.fable_lock.WATCH_DIR", d)
    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", d)
    return d


@pytest.fixture(autouse=True)
def _giw_idle_active_work(monkeypatch):  # noqa: ANN001
    """Default GIW probe: no store conductors (promotion allowed)."""

    def _idle(*_a, **_k):
        class Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {
                    "active_ops": [],
                    "write_lease": {
                        "non_terminal_conductor_count": 0,
                        "active_holders": [],
                    },
                }

        return Resp()

    monkeypatch.setattr("bus_watch.roster.httpx.get", _idle)


def _complete_row(work_key: str, *, row_id: str = "done-row") -> dict[str, Any]:
    return {
        "row_id": row_id,
        "work_key": work_key,
        "hire": "auto",
        "gate": "",
        "text": work_key,
        "live": False,
    }


@pytest.mark.offline
def test_k43_giw_active_work_default_url_matches_git_router() -> None:
    """Wrong default path → 404; roster and quiet_reason must match GIW integrate router."""
    from bus_watch import quiet_reason, roster
    from services.git_integration_worker.routes.integrate import router as git_router

    assert roster._GIW_ACTIVE_WORK == quiet_reason._GIW_ACTIVE_WORK
    path = urlparse(roster._GIW_ACTIVE_WORK).path
    assert path == f"{git_router.prefix}/active-work"


@pytest.mark.offline
def test_k43_giw_blocks_promotion_uses_single_active_work_fetch(
    watch_dir, monkeypatch
) -> None:  # noqa: ANN001
    """Two HTTP probes per check can disagree; promotion guard uses one payload."""
    root = "18290-promo-single-fetch"
    path = roster_path(root)
    append_row(path, _complete_row("todo:alpha"))
    monkeypatch.setattr(
        "bus_watch.roster_promote.scoreboard_g_rows_done", lambda wk: True
    )
    calls: list[str] = []

    def _get(url: str, *_a, **_k):
        calls.append(url)

        class Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {
                    "active_ops": [],
                    "write_lease": {
                        "non_terminal_conductor_count": 0,
                        "active_holders": [],
                    },
                }

        return Resp()

    monkeypatch.setattr("bus_watch.roster.httpx.get", _get)
    digest = {"root": {"id": root}, "roster": [_complete_row("todo:alpha")]}
    out = maybe_promote_roster(
        root,
        {"next_row": "todo:beta"},
        digest,
        {},
        cortex=lambda *_a, **_k: {},
    )
    assert out.get("action") == "appended"
    assert len(calls) == 1


def test_module_does_not_import_cortex_store() -> None:
    mod = importlib.import_module("bus_watch.roster_promote")
    source = open(mod.__file__, encoding="utf-8").read()
    assert "cortex_store" not in source


def test_t11_policy_next_row_appends_once(watch_dir, monkeypatch) -> None:  # noqa: ANN001
    root = "17981-promo-a"
    path = roster_path(root)
    append_row(path, _complete_row("todo:alpha"))
    monkeypatch.setattr(
        "bus_watch.roster_promote.scoreboard_g_rows_done", lambda wk: True
    )
    events: list[str] = []
    monkeypatch.setattr(
        "bus_watch.roster_promote._emit",
        lambda ev: events.append(ev.signal),
    )
    digest: dict[str, Any] = {
        "root": {"id": root, "recent_turns": []},
        "roster": [_complete_row("todo:alpha")],
    }
    state: dict[str, Any] = {}
    policy = {"next_row": "todo:beta G4"}
    first = maybe_promote_roster(
        root, policy, digest, state, cortex=lambda *_a, **_k: {}
    )
    assert first.get("action") == "appended"
    assert "impresario.promotion.appended" in events
    journal = merge_by_row_id(read_journal(path))
    assert len(journal) == 2
    second = maybe_promote_roster(
        root, policy, digest, state, cortex=lambda *_a, **_k: {}
    )
    assert second.get("reason") == "already_rostered"
    assert len(merge_by_row_id(read_journal(path))) == 2


def test_t13_pinned_occupancy_skips(watch_dir, monkeypatch) -> None:  # noqa: ANN001
    root = "17981-promo-b"
    path = roster_path(root)
    append_row(path, _complete_row("todo:alpha"))
    monkeypatch.setattr(
        "bus_watch.roster_promote.scoreboard_g_rows_done", lambda wk: True
    )

    def pinned(_wk: str) -> dict[str, Any]:
        return {"state": "pinned", "stop": "OPERATOR_GATE"}

    digest = {"root": {"id": root}, "roster": [_complete_row("todo:alpha")]}
    out = maybe_promote_roster(
        root,
        {"next_row": "todo:beta"},
        digest,
        {},
        cortex=lambda *_a, **_k: {},
        occupancy_fn=pinned,
    )
    assert out.get("reason") == "pinned_occupancy"
    assert len(read_journal(path)) == 1


def test_fetch_giw_live_work_keys_active_ops_and_write_lease(monkeypatch) -> None:
    from bus_watch.roster import fetch_giw_live_work_keys

    class Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "active_ops": [
                    {
                        "op_id": "disp-conductor",
                        "work_key": "todo:impresario-alpha",
                        "contract": "conductor",
                        "status": "running",
                    }
                ],
                "write_lease": {
                    "active_holders": [
                        {
                            "holder_work_key": "todo:impresario-beta",
                            "holder_contract": "conductor",
                            "holder_status": "admitted",
                        }
                    ]
                },
            }

    monkeypatch.setattr("bus_watch.roster.httpx.get", lambda *a, **k: Resp())
    live = fetch_giw_live_work_keys()
    assert live == {
        "todo:impresario-alpha": True,
        "todo:impresario-beta": True,
    }


def test_fetch_giw_non_conductor_not_live(monkeypatch) -> None:
    from bus_watch.roster import fetch_giw_live_work_keys

    class Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "holders": [
                    {
                        "work_key": "todo:alpha",
                        "contract": "implement",
                        "status": "running",
                    }
                ]
            }

    monkeypatch.setattr("bus_watch.roster.httpx.get", lambda *a, **k: Resp())
    live = fetch_giw_live_work_keys()
    assert live == {}


def test_k43_queued_conductor_blocks_second_promotion_for_same_todo(
    watch_dir, monkeypatch
) -> None:  # noqa: ANN001
    root = "18290-promo-block"
    path = roster_path(root)
    append_row(path, _complete_row("todo:alpha"))
    monkeypatch.setattr(
        "bus_watch.roster_promote.scoreboard_g_rows_done", lambda wk: True
    )

    blocked_work_key = "todo:block-x"
    payload = {
        "active_ops": [],
        "write_lease": {
            "non_terminal_conductor_count": 1,
            "active_holders": [
                {
                    "work_key": blocked_work_key,
                    "source_ref": "agent-bus:18290#block-x",
                    "contract": "conductor",
                    "holder_status": "queued",
                }
            ],
        },
    }

    def _payload(*_a, **_k):
        class Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return payload

        return Resp()

    monkeypatch.setattr("bus_watch.roster.httpx.get", _payload)
    digest = {"root": {"id": root}, "roster": [_complete_row("todo:alpha")]}
    blocked = maybe_promote_roster(
        root,
        {"next_row": blocked_work_key},
        digest,
        {},
        cortex=lambda *_a, **_k: {},
    )
    assert blocked.get("reason") == "giw_conductor_open"

    other = maybe_promote_roster(
        root,
        {"next_row": "todo:other-y"},
        digest,
        {},
        cortex=lambda *_a, **_k: {},
    )
    assert other.get("action") == "appended"


def test_k43_promotion_holds_on_store_count_mismatch(watch_dir, monkeypatch) -> None:  # noqa: ANN001
    root = "18290-promo-count"
    path = roster_path(root)
    append_row(path, _complete_row("todo:alpha"))
    monkeypatch.setattr(
        "bus_watch.roster_promote.scoreboard_g_rows_done", lambda wk: True
    )

    payload = {
        "active_ops": [],
        "write_lease": {
            "non_terminal_conductor_count": 2,
            "active_holders": [
                {
                    "work_key": "todo:only-one",
                    "contract": "conductor",
                    "holder_status": "queued",
                }
            ],
        },
    }

    def _payload(*_a, **_k):
        class Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return payload

        return Resp()

    monkeypatch.setattr("bus_watch.roster.httpx.get", _payload)
    out = maybe_promote_roster(
        root,
        {"next_row": "todo:beta"},
        {"root": {"id": root}, "roster": [_complete_row("todo:alpha")]},
        {},
        cortex=lambda *_a, **_k: {},
    )
    assert out.get("reason") == "giw_conductor_count_mismatch"
    assert len(read_journal(path)) == 1


def test_record_row_hire_skips_stale_lease_epoch(watch_dir) -> None:  # noqa: ANN001
    from bus_watch.roster import record_row_hire

    root = "17981-hire"
    path = roster_path(root)
    append_row(
        path,
        {
            "row_id": "row-a",
            "work_key": "todo:a",
            "hire": "auto",
            "lease_epoch": 3,
        },
    )
    record_row_hire(root, "row-a", "disp-new", lease_epoch=2)
    merged = merge_by_row_id(read_journal(path))
    assert merged[-1].get("last_hire_dispatch_id") != "disp-new"
    record_row_hire(root, "row-a", "disp-ok", lease_epoch=4)
    merged = merge_by_row_id(read_journal(path))
    assert merged[-1]["last_hire_dispatch_id"] == "disp-ok"
