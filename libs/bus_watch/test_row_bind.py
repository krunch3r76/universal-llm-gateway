"""CDP row-bind and ticker-fired LOW / TRIO bodies."""

from __future__ import annotations

from bus_watch.spawn_wake.fire import body_for_leftover
from bus_watch.spawn_wake.play_classify import LEFTOVER_SIT
from bus_watch.spawn_wake.row_bind import (
    body_for_sit_friction,
    build_low_implement_body,
    build_row_bind_body,
    build_trio_fire_body,
    build_trio_sketch_body,
)
from bus_watch.test_spawn_on_wake import _digest


def _forcing_digest(
    *, now_row: str = "manage recycle git_integration_worker landed"
) -> dict:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = now_row
    digest["policy"]["successor_model"] = "cursor/grok-4.7"
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


def test_sit_forcing_friction_is_row_bind_not_house_successor() -> None:
    """AC — sit + forcing friction ⇒ row-bind hop, not house generate."""
    digest = _forcing_digest()
    digest["policy"]["successor_model"] = "cursor/composer-2.5"
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
    assert body.get("model") == "cursor/grok-4.7"
    assert body.get("seat") == "cursor-sdk"
    assert body.get("model_knobs") == {"effort": "high", "fast": "false"}
    assert body.get("model") != digest["policy"]["successor_model"]


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
    assert "--mark-friction a:35997:" in body["prompt"]
    assert "friction_close" in body["prompt"]
    assert body["work_key"].startswith("row-low:a:35997:night-")
    assert body["source_ref"] == body["work_key"]
    assert body["_friction_id"] == "a:35997"


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
    assert "--mark-friction a:35997:" in body["prompt"]
    assert "friction_close" in body["prompt"]


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
    assert body["model"] == "cursor/grok-4.7"
    assert body["seat"] == "cursor-sdk"
    assert "ROW_CLASS:" in body["prompt"]
    assert "do not fire remaining hops" in body["prompt"].lower()


def test_trio_sketch_prompt_has_disposition_exit() -> None:
    body = build_trio_sketch_body(
        "11960",
        {"max_hop_minutes": 60},
        {
            "id": "a:35997",
            "category": "regression",
            "owner": "service:git_integration_worker",
            "note": "probe",
        },
    )
    assert "--mark-friction a:35997:" in body["prompt"]
    assert "friction_close" in body["prompt"]


def test_fired_fid_does_not_re_fire_low() -> None:
    friction = {
        "id": "a:35997",
        "owner": "service:git_integration_worker",
        "category": "regression",
        "note": "probe",
        "forcing": True,
        "state": "open",
    }
    digest = _forcing_digest()
    state = {
        "friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"},
        "row_class": {
            "a:35997": {"class": "low", "why": "one file", "row_id": "a:35997"}
        },
        "row_class_fired": {"a:35997": "2026-09-21T06:10:00Z"},
    }
    body = body_for_sit_friction("11960", digest["policy"], friction, state, digest)
    assert body is not None
    assert body.get("_row_class_fired") is True
    assert body.get("_refused") == "row_class_fired"


def test_trio_sketch_default_cdp() -> None:
    body = build_trio_sketch_body(
        "11960",
        {"max_hop_minutes": 60, "row_bind_model": "cursor/grok-4.7"},
        {
            "id": "a:35997",
            "category": "regression",
            "owner": "service:git_integration_worker",
            "note": "probe",
        },
    )
    assert body["model"] == "cdp/opus-5"
    assert "seat" not in body


def test_trio_sketch_paused_opus_holds() -> None:
    from work_key_grammar import is_valid_work_key_scheme

    from bus_watch.spawn_wake.row_bind import friction_night_key

    body = build_trio_sketch_body(
        "10479",
        {
            "max_hop_minutes": 60,
            "paused_models": ["cdp/opus-5", "cursor/claude-opus-5"],
        },
        {
            "id": "a:36249",
            "category": "tool_error",
            "owner": "service:cdp-ask",
            "note": "stall",
        },
    )
    assert body["_refused"] == "model_paused"
    assert "model" not in body
    key = friction_night_key("a:36249")
    assert key.startswith("friction:a:36249:night-")
    assert is_valid_work_key_scheme(key)
    trio = friction_night_key("a:36249", role="trio")
    assert trio != key
    assert is_valid_work_key_scheme(trio)


def test_trio_sketch_honours_trio_sketch_model() -> None:
    body = build_trio_sketch_body(
        "11960",
        {"max_hop_minutes": 60, "trio_sketch_model": "cdp/fable"},
        {
            "id": "a:35997",
            "category": "regression",
            "owner": "service:git_integration_worker",
            "note": "probe",
        },
    )
    assert body["model"] == "cdp/fable"


def test_fired_park_pages_once_per_fid(monkeypatch) -> None:  # noqa: ANN001
    pages: list[tuple] = []
    monkeypatch.setattr(
        "bus_watch.spawn_wake.row_bind.page_liaison", lambda *a: pages.append(a)
    )
    friction = {
        "id": "a:35997",
        "owner": "service:git_integration_worker",
        "category": "regression",
        "note": "probe",
        "forcing": True,
        "state": "open",
    }
    digest = _forcing_digest()
    state = {
        "friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"},
        "row_class": {
            "a:35997": {"class": "low", "why": "one file", "row_id": "a:35997"}
        },
        "row_class_fired": {"a:35997": "2026-09-21T06:10:00Z"},
    }
    first = body_for_sit_friction("11960", digest["policy"], friction, state, digest)
    second = body_for_sit_friction("11960", digest["policy"], friction, state, digest)
    assert first is not None and first.get("_row_class_fired") is True
    assert second is not None and second.get("_row_class_fired") is True
    assert len(pages) == 1
    assert "row_class_fired" in pages[0][1]
