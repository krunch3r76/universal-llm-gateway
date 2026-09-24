"""Friction-gate steer (hold narrow) and closeout auto-file friction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bus_watch.lane_closeout import observe_terminal_lane_closeouts
from bus_watch.spawn_on_wake import tick_spawn_on_wake
from bus_watch.spawn_wake.fire import maybe_steer_friction_gate
from bus_watch.spawn_wake.play_classify import LEFTOVER_HOLD, classify_leftover
from bus_watch.test_spawn_on_wake import _digest


def _free_lock(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock", lambda *_a, **_k: {"holder": None}
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )


def _live_sdk_lock(monkeypatch, *, ident: str = "disp-live-12700") -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock",
        lambda *_a, **_k: {
            "holder": f"sdk:{ident}",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )


def _context_budget_digest(*, holder_ident: str = "disp-live-12700") -> dict:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["budget"] = {
        "stop_class": "CONTEXT_BUDGET",
        "epoch": holder_ident,
        "as_of": "2099-01-01T00:00:00Z",
    }
    digest["lanes"] = [
        {
            "id": "12700",
            "turns": 4,
            "status": "active",
            "lifecycle": "admitted",
            "dispatch_id": holder_ident,
        }
    ]
    return digest


def _friction_hold_digest(*, friction_id: str = "a:40001") -> dict:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = (
        f"Friction {friction_id} [bug] service:bus_watch «gate blocks row»"
    )
    digest["next"] = "todo:gate-todo — blocked successor row"
    digest["lanes"] = [
        {
            "id": "12588",
            "turns": 4,
            "status": "active",
            "lifecycle": "admitted",
            "contract": "conductor",
            "work_key": "todo:gate-todo",
            "dispatch_id": "disp-gate-12588",
        }
    ]
    return digest


def test_friction_now_row_matching_live_conductor_submits_one_steer(
    monkeypatch,
) -> None:  # noqa: ANN001
    """Friction NOW on the same todo injects one steer; a second tick does not."""
    _free_lock(monkeypatch)
    digest = _friction_hold_digest()
    state: dict = {}
    submit = MagicMock(return_value=({"execution_id": "exec-1"}, 202))

    out1 = tick_spawn_on_wake(digest, state, "10479", dry_run=False, submit=submit)
    out2 = tick_spawn_on_wake(digest, state, "10479", dry_run=False, submit=submit)

    assert out1["action"] == "hold"
    assert out1["friction_gate_steer"]["steered"] is True
    assert out1["friction_gate_steer"]["friction_id"] == "a:40001"
    assert submit.call_count == 1
    body = submit.call_args.args[0]
    assert body["op"] == "steer"
    assert body["steer"] == "inject"
    assert body["dispatch_id"] == "disp-gate-12588"
    assert body["reason"] == "friction-gate"
    assert body["directive"] == "CHECKPOINT. Stop this row. Gate is a:40001."
    assert out2["action"] == "hold"
    assert out2["friction_gate_steer"]["reason"] == "latched"
    assert submit.call_count == 1
    assert state["gate_steers_sent"]["a:40001"]


def test_friction_not_now_row_does_not_submit_steer(monkeypatch) -> None:  # noqa: ANN001
    """A friction that is not NOW does not steer the live conductor."""
    _free_lock(monkeypatch)
    digest = _friction_hold_digest()
    digest["policy"]["now_row"] = "todo:gate-todo G4 play row"
    digest["frictions"] = [
        {"kind": "friction", "id": "a:40002", "owner": "service:bus_watch"}
    ]
    submit = MagicMock(return_value=({}, 202))
    leftover = classify_leftover(digest, {})
    assert leftover["leftover"] == LEFTOVER_HOLD
    meta = maybe_steer_friction_gate(digest, {}, leftover, submit=submit)
    assert meta is None
    submit.assert_not_called()


def test_consult_pending_closeout_files_friction_once() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 9}}
    client.post.return_value = resp
    lane = {
        "id": "12600",
        "lifecycle": "completed",
        "status": "closed",
    }
    turns = [
        {
            "turn_number": 9,
            "subject": "cursor-sdk CLOSEOUT",
            "body": (
                "TYPE: CLOSEOUT\n"
                "status: partial\n"
                "CONSULT_PENDING — binder needed\n"
            ),
        }
    ]
    state: dict = {}

    def fetch(_tid: str) -> list[dict]:
        return turns

    with patch("bus_watch.lane_closeout.file_closeout_friction") as file_mock:
        file_mock.return_value = ("a:50001", None)
        with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
            first = observe_terminal_lane_closeouts(
                "12586", [lane], state, client, fetch_turns=fetch
            )
            second = observe_terminal_lane_closeouts(
                "12586", [lane], state, client, fetch_turns=fetch
            )
    assert len(first) == 1
    assert first[0]["friction_id"] == "a:50001"
    assert file_mock.call_count == 1
    assert second == []


def test_closeout_with_assertion_id_skips_file_friction() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 3}}
    client.post.return_value = resp
    lane = {"id": "12601", "lifecycle": "completed", "status": "closed"}
    turns = [
        {
            "turn_number": 3,
            "subject": "CLOSEOUT",
            "body": "DEFECT: stale bind\nalready filed a:33355 in prose\n",
        }
    ]

    def fetch(_tid: str) -> list[dict]:
        return turns

    with patch("bus_watch.lane_closeout.file_closeout_friction") as file_mock:
        with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
            posted = observe_terminal_lane_closeouts(
                "12586", [lane], {}, client, fetch_turns=fetch
            )
    assert len(posted) == 1
    assert "friction_id" not in posted[0]
    file_mock.assert_not_called()


def test_live_sdk_holder_under_context_budget_steers_once(
    monkeypatch,
) -> None:  # noqa: ANN001
    """Live sdk holder under CONTEXT_BUDGET steers once; second tick latched."""
    holder_ident = "disp-live-12700"
    _live_sdk_lock(monkeypatch, ident=holder_ident)
    digest = _context_budget_digest(holder_ident=holder_ident)
    state: dict = {}
    submit = MagicMock(return_value=({"execution_id": "exec-live"}, 202))

    out1 = tick_spawn_on_wake(digest, state, "10479", dry_run=False, submit=submit)
    out2 = tick_spawn_on_wake(digest, state, "10479", dry_run=False, submit=submit)

    assert out1["action"] == "hold"
    assert out1["refused"] == "live_dispatch_hold"
    assert out1["live_dispatch_steer"]["steered"] is True
    assert submit.call_count == 1
    body = submit.call_args.args[0]
    assert body["op"] == "steer"
    assert body["steer"] == "inject"
    assert body["dispatch_id"] == holder_ident
    assert body["reason"] == "context-budget-hop"
    assert body["directive"] == (
        f"CHECKPOINT. Context budget reached for dispatch {holder_ident}. "
        "Hop under the context policy. Do not start a parallel liaison."
    )
    assert out2["action"] == "hold"
    assert out2["live_dispatch_steer"]["reason"] == "latched"
    assert submit.call_count == 1
    assert state["live_dispatch_steers_sent"][holder_ident]


def test_no_sdk_holder_still_spawns_one_generate(monkeypatch) -> None:  # noqa: ANN001
    """Without an sdk holder the ticker still posts one generate."""
    _free_lock(monkeypatch)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    state: dict = {}
    submit = MagicMock(return_value=({"execution_id": "exec-gen", "thread_id": "t1"}, 202))

    out = tick_spawn_on_wake(digest, state, "10479", dry_run=False, submit=submit)

    assert out["action"] == "spawned"
    assert submit.call_count == 1
    assert submit.call_args.args[0].get("op") != "steer"
    assert not state.get("live_dispatch_steers_sent")


def test_review_apply_body_fires_generate_under_live_holder(
    monkeypatch,
) -> None:  # noqa: ANN001
    """Owing review-apply still spawns generate under a live sdk holder."""
    from bus_watch.test_review_apply import _digest_with_review

    _live_sdk_lock(monkeypatch, ident="disp-live-review")
    digest = _digest_with_review()
    digest["budget"] = {
        "stop_class": "CONTEXT_BUDGET",
        "epoch": "disp-live-review",
        "as_of": "2099-01-01T00:00:00Z",
    }
    digest["lanes"] = [
        {
            "id": "12701",
            "turns": 2,
            "status": "active",
            "lifecycle": "admitted",
            "dispatch_id": "disp-live-review",
        }
    ]
    state: dict = {"last_spawn_at": 0.0}
    submit = MagicMock(return_value=({"execution_id": "exec-review", "thread_id": "t2"}, 202))

    out = tick_spawn_on_wake(
        digest,
        state,
        "11960",
        dry_run=False,
        submit=submit,
        is_terminal=lambda _pending: True,
    )

    assert out["action"] == "spawned"
    assert submit.call_count == 1
    assert submit.call_args.args[0].get("op") != "steer"
    assert not state.get("live_dispatch_steers_sent")


def test_live_holder_without_matching_lane_holds_without_steer(
    monkeypatch,
) -> None:  # noqa: ANN001
    """Live holder with no matching non-terminal lane holds without steer or latch."""
    holder_ident = "disp-live-12700"
    _live_sdk_lock(monkeypatch, ident=holder_ident)
    digest = _context_budget_digest(holder_ident=holder_ident)
    digest["lanes"] = [
        {
            "id": "12700",
            "turns": 9,
            "status": "closed",
            "lifecycle": "completed",
            "dispatch_id": holder_ident,
        }
    ]
    submit = MagicMock(return_value=({}, 202))
    state: dict = {}

    out = tick_spawn_on_wake(digest, state, "10479", dry_run=False, submit=submit)

    assert out["action"] == "hold"
    assert out["refused"] == "live_dispatch_hold"
    assert out["live_dispatch_steer"]["reason"] == "no_dispatch_id"
    submit.assert_not_called()
    assert not out.get("live_dispatch_steer", {}).get("steered")
    assert not state.get("live_dispatch_steers_sent")
