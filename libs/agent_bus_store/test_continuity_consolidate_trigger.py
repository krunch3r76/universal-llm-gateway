"""CLOSEOUT → consolidate-continuity trigger: gates, root resolution, payload, wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_bus_store import continuity_consolidate_trigger as trig
from agent_bus_store.db import create_thread_with_turn, init_db
from agent_bus_store.db.lane_associations import associate_lane
from agent_bus_store.db.turns import insert_turn

pytestmark = pytest.mark.offline

_CHECKPOINT_BODY = (
    "## Derived (projected at post — do not hand-edit)\n- noise\n\n"
    "## Residue (authored — cap ~800 chars)\n"
    "Mission: consolidate the house from the graph.\n"
    "In: P7 spike. Out: card regen.\n"
)


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "0")
    init_db()


def _thread(
    slug: str, *, subject: str, body: str = "x", tags: list[str] | None = None
) -> str:
    row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject=subject,
        body=body,
        tags=tags,
    )
    return row["id"]


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("CLOSEOUT — P7 landed", True),
        ("  closeout: lane done", True),
        ("CHECKPOINT — fold", False),
        ("INFO — closeout pending", False),
        (None, False),
    ],
)
def test_is_closeout_subject(subject, expected):
    assert trig.is_closeout_subject(subject) is expected


def test_resolve_root_direct_lane_and_none(bus_db):
    root = _thread(
        "house", subject="CHECKPOINT — open", body=_CHECKPOINT_BODY, tags=["role:root"]
    )
    lane = _thread("lane", subject="dispatch")
    orphan = _thread("orphan", subject="dispatch")
    associate_lane(thread_id=lane, parent_thread_id=root, lane_role="sub_mission")

    assert trig.resolve_root(root) == root
    assert trig.resolve_root(lane) == root
    assert trig.resolve_root(orphan) is None


def test_build_options_carries_residue_and_trigger(bus_db):
    root = _thread(
        "house", subject="CHECKPOINT — open", body=_CHECKPOINT_BODY, tags=["role:root"]
    )
    lane = _thread("lane", subject="dispatch")
    associate_lane(thread_id=lane, parent_thread_id=root, lane_role="sub_mission")
    _, _, turn_number = insert_turn(
        thread=lane,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="CLOSEOUT — done",
        body="commit: abc\nverdict: PASS",
    )

    options = trig.build_consolidate_options(
        root=root, trigger_thread=lane, turn_number=turn_number
    )

    assert options["root_thread"] == root
    assert options["root"]["tags"] == ["role:root"]
    assert options["tip_checkpoint"]["turn"] == 1
    assert options["tip_checkpoint"]["residue"].startswith(
        "Mission: consolidate the house"
    )
    assert "## Derived" not in options["tip_checkpoint"]["residue"]
    assert options["trigger"] == {
        "thread": lane,
        "turn": turn_number,
        "from_agent": "cursor-sdk",
        "created_at": options["trigger"]["created_at"],
        "subject": "CLOSEOUT — done",
        "body": "commit: abc\nverdict: PASS",
    }


def test_maybe_enqueue_gates_and_defers_to_worker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "1")
    seen: list[tuple[str, int]] = []
    with patch.object(trig.threading, "Thread") as thread_cls:
        thread_cls.return_value.start.side_effect = lambda: seen.append(
            thread_cls.call_args.kwargs["args"]
        )
        assert trig.maybe_enqueue_continuity_consolidate(
            thread_id="10303", turn_number=54, subject="CLOSEOUT — x"
        )
        assert not trig.maybe_enqueue_continuity_consolidate(
            thread_id="10303", turn_number=55, subject="INFO — x"
        )
    assert seen == [("10303", 54)]
    assert thread_cls.call_args.kwargs["daemon"] is True

    monkeypatch.setenv("AGENT_BUS_CONTINUITY_CONSOLIDATE", "0")
    assert not trig.maybe_enqueue_continuity_consolidate(
        thread_id="10303", turn_number=56, subject="CLOSEOUT — x"
    )


def test_insert_turn_invokes_trigger(bus_db):
    lane = _thread("lane", subject="dispatch")
    with patch.object(
        trig, "maybe_enqueue_continuity_consolidate", return_value=False
    ) as hook:
        _, _, turn_number = insert_turn(
            thread=lane,
            from_agent="cursor-sdk",
            to_agent="cursor",
            subject="CLOSEOUT — done",
            body="ok",
        )
    hook.assert_called_once_with(
        thread_id=lane, turn_number=turn_number, subject="CLOSEOUT — done"
    )


def test_worker_never_raises(bus_db):
    with patch.object(trig, "resolve_root", side_effect=RuntimeError("db gone")):
        trig._run("10303", 54)
