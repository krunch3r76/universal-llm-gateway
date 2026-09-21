"""ROW_CLASS parse and latch for liaison ticker two-path law."""

from __future__ import annotations

from bus_watch.spawn_wake.row_class import (
    ROW_CLASS_HOLD,
    ROW_CLASS_TRIO,
    absorb_row_classes_from_digest,
    latched_row_class,
    parse_row_class,
    row_class_hold,
    undispositioned_forcing_friction,
)


def _forcing_friction(aid: int = 35997) -> dict:
    return {
        "id": f"a:{aid}",
        "owner": "service:git_integration_worker",
        "category": "regression",
        "note": "GIW HTTP probe false-unhealthy",
        "state": "open",
        "forcing": True,
    }


def test_parse_row_class_trio() -> None:
    parsed = parse_row_class(
        "ROW_CLASS: trio\nROW_WHY: multi-hop arc\nROW_ID: a:35997\n"
    )
    assert parsed == {
        "class": "trio",
        "why": "multi-hop arc",
        "row_id": "a:35997",
    }


def test_parse_row_class_ambiguous() -> None:
    assert parse_row_class("ROW_CLASS: maybe\n") is None
    assert parse_row_class("no class here") is None


def test_absorb_latches_from_root_turns() -> None:
    state: dict = {}
    digest = {
        "frictions": [_forcing_friction()],
        "root": {
            "id": "11960",
            "recent_turns": [
                {
                    "turn_number": 56,
                    "from": "cdp",
                    "body": "ROW_CLASS: low\nROW_WHY: mechanical fix\nROW_ID: a:35997\n",
                    "created_at": "2026-09-21T06:00:00Z",
                }
            ],
        },
    }
    absorb_row_classes_from_digest(digest, state)
    latched = latched_row_class(state, "a:35997")
    assert latched is not None
    assert latched["class"] == "low"
    assert latched["why"] == "mechanical fix"


def test_row_class_hold_after_bind_without_class() -> None:
    state = {"friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"}}
    assert row_class_hold(state, "a:35997") is True


def test_row_class_hold_not_before_bind() -> None:
    state: dict = {}
    assert row_class_hold(state, "a:35997") is False


def test_undispositioned_forcing_skips_close_pending() -> None:
    digest = {
        "frictions": [
            {**_forcing_friction(), "state": "close_pending", "forcing": True},
            _forcing_friction(36073),
        ]
    }
    row = undispositioned_forcing_friction(digest)
    assert row is not None
    assert row["id"] == "a:36073"


def test_fire_spawn_refused_hold_constant() -> None:
    assert ROW_CLASS_HOLD == "row_class_hold"


def test_latched_trio_class() -> None:
    state = {
        "row_class": {
            "a:35997": {"class": ROW_CLASS_TRIO, "why": "sketch path", "row_id": "a:35997"}
        }
    }
    latched = latched_row_class(state, "a:35997")
    assert latched is not None
    assert latched["class"] == ROW_CLASS_TRIO
