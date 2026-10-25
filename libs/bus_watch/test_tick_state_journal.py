"""Journal rows for operator and ticker now-row writes via update_state."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bus_watch.now_row_bind import maybe_bind_now_row
from bus_watch.tick_state import save_state, update_state


def _tick(path: Path, *, now_row: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_state(
        path,
        {"policy": {"now_row": now_row}, "register": "attended"},
    )


def test_operator_now_row_set_writes_one_row_actor_operator(tmp_path: Path) -> None:
    path = tmp_path / "liaison-10479.tick.json"
    _tick(path, now_row="todo:old")

    def mutate(state: dict) -> None:
        policy = dict(state.get("policy") or {})
        policy["now_row"] = "todo:new"
        state["policy"] = policy

    with patch("bus_watch.house_journal.append_house_action") as append:
        update_state(path, mutate, journal_actor="operator")
        append.assert_called_once_with("10479", "now_row_set", "operator", "todo:new")


def test_ticker_bind_writes_one_row_actor_ticker_and_loop_save_adds_none(
    tmp_path: Path,
) -> None:
    path = tmp_path / "liaison-10479.tick.json"
    _tick(path, now_row="")
    digest = {
        "root": {"id": "10479"},
        "policy": {"now_row": ""},
        "policy_entity_cache": {},
        "attention": [{"id": "9001", "last_subject": "NOW todo:bind"}],
        "lanes": [
            {
                "id": "9001",
                "last_subject": "NOW todo:bind",
                "status": "active",
            }
        ],
    }
    state = {"policy": {"now_row": ""}}

    with (
        patch(
            "bus_watch.now_row_bind.resolve_now_row",
            return_value=("todo:bind", "attention"),
        ),
        patch("bus_watch.house_journal.append_house_action") as append,
    ):
        maybe_bind_now_row(digest, state, path, as_of="2026-10-25T00:00:00Z")
        append.assert_called_once()
        args = append.call_args.args
        assert args[0] == "10479"
        assert args[2] == "ticker"
        save_state(path, state)
        assert append.call_count == 1


def test_unchanged_now_row_writes_no_row(tmp_path: Path) -> None:
    path = tmp_path / "liaison-10479.tick.json"
    _tick(path, now_row="todo:same")

    def mutate(state: dict) -> None:
        state["last_cp_tick"] = 3

    with patch("bus_watch.house_journal.append_house_action") as append:
        update_state(path, mutate, journal_actor="operator")
        append.assert_not_called()


def test_mark_relayed_only_edit_writes_no_row(tmp_path: Path) -> None:
    path = tmp_path / "liaison-10479.tick.json"
    _tick(path, now_row="todo:keep")

    def mutate(state: dict) -> None:
        state["relayed_watchers"] = ["w1"]

    with patch("bus_watch.house_journal.append_house_action") as append:
        update_state(path, mutate, journal_actor="operator")
        append.assert_not_called()


def test_birth_save_state_writes_no_row(tmp_path: Path) -> None:
    path = tmp_path / "liaison-20468.tick.json"
    with patch("bus_watch.house_journal.append_house_action") as append:
        save_state(
            path,
            {
                "policy": {
                    "now_row": "ruling:grok-sdk-fast-off",
                    "single_mission_todo": "ruling:grok-sdk-fast-off",
                }
            },
        )
        append.assert_not_called()
