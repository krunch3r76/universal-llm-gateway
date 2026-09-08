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


class _HeldTimer:
    """``threading.Timer`` stand-in — tests ``fire()`` instead of sleeping the interval."""

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.args = tuple(args or ())
        self.kwargs = dict(kwargs or {})
        self.daemon = False
        self.cancelled = False

    def start(self) -> None:
        return None

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if self.cancelled:
            return
        self.function(*self.args, **self.kwargs)


def _house(slug: str) -> tuple[str, str]:
    root = _thread(
        f"{slug}-house",
        subject="CHECKPOINT — open",
        body=_CHECKPOINT_BODY,
        tags=["role:root"],
    )
    lane = _thread(f"{slug}-lane", subject="dispatch")
    associate_lane(thread_id=lane, parent_thread_id=root, lane_role="sub_mission")
    return root, lane


def _closeout(thread_id: str, *, body: str = "ok") -> int:
    _, _, turn_number = insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="CLOSEOUT — done",
        body=body,
    )
    return turn_number


def _arm_short_debounce(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.05) -> float:
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_DEBOUNCE_S", str(seconds))
    if hasattr(trig, "DEBOUNCE_S"):
        monkeypatch.setattr(trig, "DEBOUNCE_S", seconds)
    if hasattr(trig, "_DEBOUNCE_S"):
        monkeypatch.setattr(trig, "_DEBOUNCE_S", seconds)
    return seconds


def _held_timer_factory(held: list[_HeldTimer]):
    def factory(interval, function, args=None, kwargs=None):
        timer = _HeldTimer(interval, function, args, kwargs)
        held.append(timer)
        return timer

    return factory


def _live_timers(held: list[_HeldTimer]) -> list[_HeldTimer]:
    return [t for t in held if not t.cancelled]


def _enqueue_options(call) -> dict:
    if call.args:
        return call.args[0]
    for key in ("options", "pipeline_options"):
        if key in call.kwargs:
            return call.kwargs[key]
    raise AssertionError(f"enqueue_consolidate call is not options-shaped: {call}")


@pytest.fixture()
def burst_harness(bus_db, monkeypatch: pytest.MonkeyPatch):
    """Short debounce env + held Timer + mocked ``enqueue_consolidate``."""
    _arm_short_debounce(monkeypatch)
    held: list[_HeldTimer] = []
    with (
        patch("threading.Timer", _held_timer_factory(held)),
        patch.object(trig, "enqueue_consolidate", return_value="exec-1") as enq,
    ):
        yield held, enq


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
    money = _thread(
        "money",
        subject="CHECKPOINT — open",
        body=_CHECKPOINT_BODY,
        tags=["role:root"],
    )
    lane = _thread("lane", subject="dispatch")
    orphan = _thread("orphan", subject="dispatch")
    associate_lane(thread_id=lane, parent_thread_id=root, lane_role="sub_mission")

    assert trig.resolve_root(root) == root
    assert trig.resolve_root(money) == money
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


def test_burst_coalesces_to_single_dispatch(bus_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_DEBOUNCE_S", "0.05")
    trig.reset_debounce_state()
    root = _thread(
        "house", subject="CHECKPOINT — open", body=_CHECKPOINT_BODY, tags=["role:root"]
    )
    lane = _thread("lane", subject="dispatch")
    associate_lane(thread_id=lane, parent_thread_id=root, lane_role="sub_mission")
    turn_numbers: list[int] = []
    for _ in range(3):
        _, _, turn_number = insert_turn(
            thread=lane,
            from_agent="cursor-sdk",
            to_agent="cursor",
            subject="CLOSEOUT — burst",
            body="ok",
        )
        turn_numbers.append(turn_number)
    enqueued: list[dict] = []

    def _capture(options: dict) -> str:
        enqueued.append(options)
        return "exec-1"

    with patch.object(trig, "enqueue_consolidate", side_effect=_capture):
        for turn in turn_numbers:
            trig._run(lane, turn)
        trig._timers[root].join(timeout=1.0)

    assert len(enqueued) == 1
    assert enqueued[0]["trigger"]["turn"] == turn_numbers[-1]
    trig.reset_debounce_state()


def test_cross_root_independent_debounce(bus_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_DEBOUNCE_S", "0.05")
    trig.reset_debounce_state()
    root_a = _thread(
        "house-a",
        subject="CHECKPOINT — open",
        body=_CHECKPOINT_BODY,
        tags=["role:root"],
    )
    root_b = _thread(
        "house-b",
        subject="CHECKPOINT — open",
        body=_CHECKPOINT_BODY,
        tags=["role:root"],
    )
    lane_a = _thread("lane-a", subject="dispatch")
    lane_b = _thread("lane-b", subject="dispatch")
    associate_lane(thread_id=lane_a, parent_thread_id=root_a, lane_role="sub_mission")
    associate_lane(thread_id=lane_b, parent_thread_id=root_b, lane_role="sub_mission")
    _, _, turn_a = insert_turn(
        thread=lane_a,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="CLOSEOUT — a",
        body="ok",
    )
    _, _, turn_b = insert_turn(
        thread=lane_b,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="CLOSEOUT — b",
        body="ok",
    )
    enqueued: list[str] = []

    with patch.object(
        trig, "enqueue_consolidate", side_effect=lambda _opts: enqueued.append("x")
    ):
        trig._run(lane_a, turn_a)
        trig._run(lane_b, turn_b)
        trig._timers[root_a].join(timeout=1.0)
        trig._timers[root_b].join(timeout=1.0)

    assert len(enqueued) == 2
    trig.reset_debounce_state()


def test_debounce_second_timer_tick_after_clear_is_noop(bus_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_CONTINUITY_DEBOUNCE_S", "0.05")
    trig.reset_debounce_state()
    root = _thread(
        "house", subject="CHECKPOINT — open", body=_CHECKPOINT_BODY, tags=["role:root"]
    )
    lane = _thread("lane", subject="dispatch")
    associate_lane(thread_id=lane, parent_thread_id=root, lane_role="sub_mission")
    _, _, turn_number = insert_turn(
        thread=lane,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="CLOSEOUT — once",
        body="ok",
    )
    calls: list[str] = []

    with patch.object(
        trig, "enqueue_consolidate", side_effect=lambda _opts: calls.append("x") or "e"
    ):
        trig._run(lane, turn_number)
        trig._timers[root].join(timeout=1.0)
        trig._fire_pending(root)
    assert len(calls) == 1
    trig.reset_debounce_state()
