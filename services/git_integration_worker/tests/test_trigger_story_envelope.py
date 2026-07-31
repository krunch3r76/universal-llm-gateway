"""Story envelope election, stamping, allowlist, and projector negative AC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from systems.frontier_consult.story_wire import (
    DEFAULT_TRIGGER_PURPOSE,
    PURPOSE_UNSTATED,
)

from services.git_integration_worker.trigger_service.models import TriggerRow
from services.git_integration_worker.trigger_service.store import TriggerStore
from services.git_integration_worker.trigger_service.story_envelope import (
    STORY_ID_SOURCE_VOCABULARY,
    elect_trigger_story_envelope,
    emit_trigger_signal,
    stamp_degrade_count,
    stamp_trigger_envelope,
)
from services.git_integration_worker.ulg_story_projector.allowlist import (
    SIGNAL_ALLOWLIST,
    SIGNAL_MAPPINGS,
)
from services.git_integration_worker.ulg_story_projector.render import render_event_line


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    return TriggerStore()


def _row(**kwargs: object) -> TriggerRow:
    base = {
        "id": "abc123",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "life-seat",
        "fire_at": "2026-01-01T00:05:00+00:00",
        "prompt_uri": "cortex://notes/system/threads/p.md",
        "purpose": DEFAULT_TRIGGER_PURPOSE,
        "model": "opus-5",
        "arc": None,
        "so_what": None,
        "status": "scheduled",
        "attempts": 0,
        "max_attempts": 3,
        "last_error": None,
        "claimed_at": None,
        "execution_id": None,
        "fired_at": None,
        "terminal_status": None,
        "archive_uri": None,
        "cancelled_at": None,
        "predicate": None,
        "predicate_args": None,
        "expires_at": None,
        "last_predicate_error": None,
    }
    base.update(kwargs)
    return TriggerRow(**base)  # type: ignore[arg-type]


def test_elect_mission_arc(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="life-seat",
        fire_at=datetime.now(UTC) + timedelta(minutes=5),
        prompt_uri="cortex://notes/system/threads/p.md",
        arc="mission-foo",
    )
    assert row.story_id == "mission-foo"
    assert row.story_id_source == "mission_arc"


def test_elect_charter_window(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="life-seat",
        fire_at=datetime.now(UTC) + timedelta(minutes=5),
        prompt_uri="cortex://notes/system/threads/p.md",
        charter_root="6237",
        window_index=24,
    )
    assert row.story_id == "6237#24"
    assert row.story_id_source == "charter_window"


def test_elect_fallback_trigger(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="life-seat",
        fire_at=datetime.now(UTC) + timedelta(minutes=5),
        prompt_uri="cortex://notes/system/threads/p.md",
    )
    assert row.story_id == row.id
    assert row.story_id_source == "fallback_trigger"


def test_nest_under_does_not_change_election() -> None:
    a = elect_trigger_story_envelope(
        trigger_id="t1",
        created_by="x",
        arc="mission-a",
        nest_under="should-not-matter",
    )
    b = elect_trigger_story_envelope(
        trigger_id="t1",
        created_by="x",
        arc="mission-a",
    )
    assert a.story_id == b.story_id == "mission-a"


def test_purpose_resolution_so_what() -> None:
    env = elect_trigger_story_envelope(
        trigger_id="t1",
        created_by="x",
        purpose=DEFAULT_TRIGGER_PURPOSE,
        so_what="follow up on charter",
    )
    assert env.purpose == "follow up on charter"
    assert env.purpose_source == "so_what"


def test_purpose_resolution_unstated() -> None:
    env = elect_trigger_story_envelope(
        trigger_id="t1",
        created_by="x",
        purpose=DEFAULT_TRIGGER_PURPOSE,
    )
    assert env.purpose == PURPOSE_UNSTATED
    assert env.purpose_source == "unstated"


def test_purpose_named_constant_pin() -> None:
    assert DEFAULT_TRIGGER_PURPOSE == "operator-proxy"


def test_unelected_distinct_story_ids(store: TriggerStore) -> None:
    r1 = store.schedule(
        created_by="x",
        fire_at=datetime.now(UTC) + timedelta(minutes=1),
        prompt_uri="cortex://notes/system/threads/a.md",
    )
    r2 = store.schedule(
        created_by="x",
        fire_at=datetime.now(UTC) + timedelta(minutes=2),
        prompt_uri="cortex://notes/system/threads/b.md",
    )
    assert r1.story_id != r2.story_id
    assert "(unelected)" not in (r1.story_id, r2.story_id)


def test_legacy_null_stamp_unelected() -> None:
    row = _row(story_id=None, story_id_source=None)
    payload: dict[str, str] = {}
    stamp_trigger_envelope(payload, row)
    assert payload["story_id"] == "abc123"
    assert payload["story_id_source"] == "unelected"
    assert payload["purpose_source"] == "unstated"


def test_stamp_mandatory_keys() -> None:
    row = _row(story_id="mission-x", story_id_source="mission_arc", so_what="do thing")
    payload: dict[str, object] = {"trigger_id": row.id}
    stamp_trigger_envelope(payload, row)
    for key in (
        "story_id",
        "story_id_source",
        "asked_by",
        "purpose",
        "purpose_source",
    ):
        assert key in payload


def test_allowlist_four_giw_signals() -> None:
    for sig in (
        "giw.trigger.fired",
        "giw.trigger.reconciled",
        "giw.trigger.fire_failed",
        "giw.trigger.reclaimed",
    ):
        assert sig in SIGNAL_ALLOWLIST
        assert sig in SIGNAL_MAPPINGS
    assert "giw.trigger.claimed" not in SIGNAL_ALLOWLIST


def test_emit_fired_carries_envelope(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="life-seat",
        fire_at=datetime.now(UTC) + timedelta(minutes=5),
        prompt_uri="cortex://notes/system/threads/p.md",
        arc="mission-y",
        so_what="relay proof",
    )
    published: list[dict] = []
    emit_trigger_signal(
        "giw.trigger.fired",
        row,
        publish=lambda _s, p: published.append(p),
        execution_id="exec-1",
    )
    assert published[0]["story_id"] == "mission-y"
    assert published[0]["story_id_source"] == "mission_arc"


def test_degrade_sentinel_on_stamp_failure() -> None:
    row = _row(story_id="x", story_id_source="mission_arc")
    published: list[dict] = []

    with patch(
        "services.git_integration_worker.trigger_service.story_envelope.stamp_trigger_envelope",
        side_effect=RuntimeError("forced"),
    ):
        emit_trigger_signal(
            "giw.trigger.fired",
            row,
            publish=lambda _s, p: published.append(p),
        )
    assert published
    assert published[0]["story_id"] == row.id
    assert published[0]["story_id_source"] == "unelected"
    assert stamp_degrade_count() >= 1


def test_projector_batch_negative_no_neighbor_inherit() -> None:
    """S-B8: absent story_id must not inherit mission-X from neighbors."""
    line1 = render_event_line(
        seq=1,
        signal="giw.trigger.fired",
        payload={
            "trigger_id": "trigger-a",
            "execution_id": "e1",
            "story_id": "mission-X",
            "story_id_source": "mission_arc",
            "asked_by": "life-seat",
            "purpose": "task a",
            "purpose_source": "purpose",
        },
    )
    line2 = render_event_line(
        seq=2,
        signal="giw.trigger.fired",
        payload={
            "trigger_id": "trigger-b",
            "execution_id": "e2",
            "asked_by": "life-seat",
            "purpose": "task b",
            "purpose_source": "purpose",
        },
    )
    line3 = render_event_line(
        seq=3,
        signal="giw.trigger.reconciled",
        payload={
            "trigger_id": "trigger-a",
            "terminal_status": "completed",
            "story_id": "mission-X",
            "story_id_source": "mission_arc",
            "asked_by": "life-seat",
            "purpose": "task a",
            "purpose_source": "purpose",
        },
    )
    assert line1 is not None and line2 is not None and line3 is not None
    assert "story:mission-X" in line1
    assert "story:trigger-b" in line2
    assert "mission-X" not in line2
    assert "story:mission-X" in line3


def test_reclaimed_envelope_from_row(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="life-seat",
        fire_at=datetime.now(UTC) + timedelta(minutes=1),
        prompt_uri="cortex://notes/system/threads/p.md",
        arc="mission-z",
    )
    published: list[dict] = []
    emit_trigger_signal(
        "giw.trigger.reclaimed",
        row,
        publish=lambda _s, p: published.append(p),
        claimed_at="2026-01-01T00:00:00+00:00",
    )
    assert published[0]["story_id"] == "mission-z"
    assert published[0]["story_id_source"] == "mission_arc"


def test_vocabulary_closed() -> None:
    assert STORY_ID_SOURCE_VOCABULARY == frozenset(
        {"charter_window", "mission_arc", "fallback_trigger", "unelected"}
    )


def test_render_fired_reconciled_share_story_id() -> None:
    base = {
        "trigger_id": "tid1",
        "story_id": "mission-shared",
        "story_id_source": "mission_arc",
        "asked_by": "life-seat",
        "purpose": "dogfood proof",
        "purpose_source": "purpose",
    }
    fired = render_event_line(
        seq=10,
        signal="giw.trigger.fired",
        payload={**base, "execution_id": "ex1"},
    )
    reconciled = render_event_line(
        seq=11,
        signal="giw.trigger.reconciled",
        payload={**base, "terminal_status": "completed"},
    )
    assert fired and reconciled
    assert "story:mission-shared" in fired
    assert "story:mission-shared" in reconciled
