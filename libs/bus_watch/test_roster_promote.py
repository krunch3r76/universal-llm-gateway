"""Roster promotion — fork 10 (T11–T14)."""

from __future__ import annotations

import importlib
from typing import Any

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


def _complete_row(work_key: str, *, row_id: str = "done-row") -> dict[str, Any]:
    return {
        "row_id": row_id,
        "work_key": work_key,
        "hire": "auto",
        "gate": "",
        "text": work_key,
        "live": False,
    }


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
