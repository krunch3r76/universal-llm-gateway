"""ROW_CLASS parse and latch for liaison ticker two-path law."""

from __future__ import annotations

from bus_watch.spawn_wake.row_class import (
    ROW_CLASS_HOLD,
    ROW_CLASS_TRIO,
    absorb_row_classes_from_digest,
    latched_row_class,
    mark_row_class_fired,
    parse_row_class,
    promote_friction_attention,
    ready_row_class_attention,
    ready_to_fire_row_class,
    row_class_fired,
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
    parsed = parse_row_class("ROW_CLASS: maybe\nROW_ID: a:35997\n")
    assert parsed == {"class": ROW_CLASS_HOLD, "row_id": "a:35997"}
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
    """Producer rows set forcing from state; close_pending is not forcing."""
    digest = {
        "frictions": [
            {**_forcing_friction(), "state": "close_pending", "forcing": False},
            _forcing_friction(36073),
        ]
    }
    row = undispositioned_forcing_friction(digest)
    assert row is not None
    assert row["id"] == "a:36073"


def test_absorb_newest_turn_number_wins_over_feed_order() -> None:
    """S3 — sort on turn_number, not transport list order."""
    state: dict = {}
    digest = {
        "frictions": [_forcing_friction()],
        "root": {
            "unread_turns": [
                {
                    "turn_number": 56,
                    "body": "ROW_CLASS: trio\nROW_ID: a:35997\n",
                },
                {
                    "turn_number": 57,
                    "body": "ROW_CLASS: low\nROW_ID: a:35997\n",
                },
            ]
        },
    }
    absorb_row_classes_from_digest(digest, state)
    latched = latched_row_class(state, "a:35997")
    assert latched is not None
    assert latched["class"] == "low"


def test_absorb_row_id_wins_over_why_mention() -> None:
    """S2 — ROW_WHY naming another a: must not steal the latch."""
    state: dict = {}
    digest = {
        "frictions": [_forcing_friction(), _forcing_friction(36073)],
        "root": {
            "recent_turns": [
                {
                    "turn_number": 60,
                    "body": (
                        "ROW_CLASS: low\n"
                        "ROW_WHY: same shape as a:36073\n"
                        "ROW_ID: a:35997\n"
                    ),
                }
            ]
        },
    }
    absorb_row_classes_from_digest(digest, state)
    assert latched_row_class(state, "a:35997") is not None
    assert latched_row_class(state, "a:36073") is None


def test_absorb_unattributed_does_not_fallback_to_newest_forcing() -> None:
    """S6 — no a: in ROW_ID or subject ⇒ skip; do not pin newest-forcing."""
    state: dict = {}
    digest = {
        "frictions": [_forcing_friction()],
        "root": {
            "recent_turns": [
                {
                    "turn_number": 61,
                    "subject": "ROW_CLASS bind",
                    "body": "ROW_CLASS: low\nROW_WHY: same shape as a:36073\n",
                }
            ]
        },
    }
    absorb_row_classes_from_digest(digest, state)
    assert latched_row_class(state, "a:35997") is None
    assert latched_row_class(state, "a:36073") is None
    assert not state.get("row_class")


def test_ambiguous_class_holds_instead_of_rebinding() -> None:
    state = {"friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"}}
    digest = {
        "frictions": [_forcing_friction()],
        "root": {
            "recent_turns": [
                {
                    "turn_number": 70,
                    "body": "ROW_CLASS: low\nROW_ID: a:35997\n",
                },
                {
                    "turn_number": 71,
                    "body": "ROW_CLASS: maybe\nROW_ID: a:35997\n",
                },
            ]
        },
    }
    absorb_row_classes_from_digest(digest, state)
    assert latched_row_class(state, "a:35997") is None
    assert row_class_hold(state, "a:35997") is True


def test_fire_spawn_refused_hold_constant() -> None:
    assert ROW_CLASS_HOLD == "row_class_hold"


def test_latched_trio_class() -> None:
    state = {
        "row_class": {
            "a:35997": {
                "class": ROW_CLASS_TRIO,
                "why": "sketch path",
                "row_id": "a:35997",
            }
        }
    }
    latched = latched_row_class(state, "a:35997")
    assert latched is not None
    assert latched["class"] == ROW_CLASS_TRIO


def test_ready_attention_wires_promote_when_latched_unfired() -> None:
    friction = _forcing_friction()
    state = {
        "friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"},
        "row_class": {
            "a:35997": {"class": "low", "why": "mechanical", "row_id": "a:35997"}
        },
    }
    digest = {"frictions": [friction], "root": {"recent_turns": []}}
    items = ready_row_class_attention(digest, state)
    assert items == promote_friction_attention(digest, friction)
    assert ready_to_fire_row_class(state, "a:35997") is True
    mark_row_class_fired(state, "a:35997", at="2026-09-21T06:11:00Z")
    assert row_class_fired(state, "a:35997") is True
    assert ready_to_fire_row_class(state, "a:35997") is False
    assert ready_row_class_attention(digest, state) == []
