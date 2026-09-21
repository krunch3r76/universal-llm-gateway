"""CDP row-bind and ticker-fired LOW / TRIO bodies."""

from __future__ import annotations

from bus_watch.spawn_wake.fire import body_for_leftover
from bus_watch.spawn_wake.play_classify import LEFTOVER_SIT
from bus_watch.spawn_wake.row_bind import (
    build_low_implement_body,
    build_row_bind_body,
    build_trio_fire_body,
)
from bus_watch.test_spawn_on_wake import _digest


def _forcing_digest(*, now_row: str = "manage recycle git_integration_worker landed") -> dict:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = now_row
    digest["policy"]["successor_model"] = "cursor/grok-4.6"
    digest["frictions"] = [
        {
            "id": "a:35997",
            "owner": "service:git_integration_worker",
            "category": "regression",
            "note": "GIW HTTP probe false-unhealthy",
            "state": "open",
            "forcing": True,
        }
    ]
    digest["lanes"] = []
    return digest


def test_sit_forcing_friction_is_row_bind_not_grok() -> None:
    """AC — sit + recycle now_row + forcing friction ⇒ CDP row-bind, no grok."""
    digest = _forcing_digest()
    leftover = {"leftover": LEFTOVER_SIT, "reason": "sit_no_todo", "todo": None}
    body = body_for_leftover(
        "11960",
        digest["policy"],
        leftover,
        successor_context={"row": digest["policy"]["now_row"]},
        digest=digest,
        state={},
    )
    assert body is not None
    assert body.get("_row_bind") is True
    assert body.get("model") == "cdp/opus-5"
    assert "seat" not in body
    assert "lane" not in body
    assert body.get("model") != "cursor/grok-4.6"


def test_sit_forcing_without_friction_in_successor_row() -> None:
    """AC — digest.frictions suffices when successor_context.row lacks Friction a:."""
    digest = _forcing_digest(now_row="Settled · Live · Next")
    leftover = {"leftover": LEFTOVER_SIT, "reason": "sit_no_todo", "todo": None}
    body = body_for_leftover(
        "11960",
        digest["policy"],
        leftover,
        successor_context={"row": "Settled · Live · Next"},
        digest=digest,
        state={},
    )
    assert body is not None
    assert body.get("_row_bind") is True


def test_latched_low_is_implement_lane_b() -> None:
    friction = {
        "id": "a:35997",
        "owner": "service:git_integration_worker",
        "category": "regression",
        "note": "probe",
    }
    body = build_low_implement_body(
        "11960",
        {"max_hop_minutes": 60},
        friction,
        latched={"class": "low", "why": "one-file fix"},
    )
    assert body["contract"] == "implement"
    assert body["lane"] == "B"
    assert body["seat"] == "cursor-sdk"
    assert "land on green" in body["prompt"].lower()


def test_latched_trio_with_todo_is_play_body() -> None:
    friction = {
        "id": "a:35997",
        "owner": "service:git_integration_worker",
        "category": "regression",
        "note": "probe",
    }
    body = build_trio_fire_body(
        "11960",
        {"max_hop_minutes": 60, "gear": "3-wake-on-attention"},
        friction,
        latched={"class": "trio", "row_id": "todo:foo"},
        todo_slug="foo",
    )
    assert body["contract"] == "conductor"
    assert body["source_ref"] == "todo:foo"
    assert body["lane"] == "B"
    assert body.get("_row_class") == "trio"


def test_build_row_bind_body_shape() -> None:
    body = build_row_bind_body(
        "11960",
        {"max_hop_minutes": 60},
        {
            "id": "a:35997",
            "category": "regression",
            "owner": "service:git_integration_worker",
            "note": "probe",
        },
    )
    assert body["model"] == "cdp/opus-5"
    assert "ROW_CLASS:" in body["prompt"]
    assert "do not fire remaining hops" in body["prompt"].lower()
