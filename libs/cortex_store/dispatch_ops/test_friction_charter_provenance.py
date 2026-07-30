"""G0 — friction charter provenance round-trip + frictions filter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops._friction_enqueue import reconcile_charter_frictions
from cortex_store.dispatch_ops.ops_assertions import _op_frictions
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get
from cortex_store.dispatch_ops.ops_assertions_write import _op_friction


def _seed_service(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
        ("service:charter-runner", "charter-runner"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
        ("service:agent-bus", "agent-bus"),
    )
    conn.commit()


@pytest.fixture()
def friction_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        _seed_service(conn)


@pytest.mark.offline
def test_friction_provenance_round_trip(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="checkpoint missing on autonomous window",
        agent="cursor-sdk",
        session_id="web-2026-07-25-test",
        charter_root="5624",
        window_index=3,
        scoreboard_uri="cortex://notes/system/threads/scoreboard.md",
        actionable=True,
    )
    assert "error" not in result, result
    item = result.get("item") or {}
    aid = int(item["id"])
    got = _op_assertion_get(assertion_id=aid)
    attrs = got.get("attributes") or {}
    assert attrs.get("charter_root") == "5624"
    assert attrs.get("window_index") == 3
    assert attrs.get("session_id") == "web-2026-07-25-test"
    assert attrs.get("actionable") is True


@pytest.mark.offline
def test_frictions_charter_root_filter(friction_db: None) -> None:
    _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="root 5624",
        agent="t",
        charter_root="5624",
        window_index=1,
    )
    _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="root 5812",
        agent="t",
        charter_root="5812",
        window_index=1,
    )
    rows = _op_frictions(charter_root="5624", limit=20, intent="summary")
    assert "error" not in rows
    items = rows.get("items") or []
    assert items
    for row in items:
        assert row["attributes"]["charter_root"] == "5624"


@pytest.mark.offline
def test_frictions_window_index_filter(friction_db: None) -> None:
    _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="w1",
        agent="t",
        charter_root="5624",
        window_index=1,
    )
    _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="w2",
        agent="t",
        charter_root="5624",
        window_index=2,
    )
    rows = _op_frictions(charter_root="5624", window_index=2, intent="summary")
    assert len(rows.get("items") or []) == 1
    assert rows["items"][0]["attributes"]["window_index"] == 2


@pytest.mark.offline
def test_actionable_false_requires_reason(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="informational only",
        agent="t",
        charter_root="5624",
        window_index=1,
        actionable=False,
    )
    assert "error" in result
    assert "actionable_false_reason" in result["error"]

    ok = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="informational only",
        agent="t",
        charter_root="5624",
        window_index=1,
        actionable=False,
        actionable_false_reason="no material defect this window",
    )
    assert "error" not in ok


@pytest.mark.offline
def test_protocol_friction_without_charter_attrs_rejects(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="missing charter context",
        agent="t",
    )
    assert "error" in result
    assert "file_charter_protocol_friction" in result["error"]
    assert result.get("item") is None


@pytest.mark.offline
def test_protocol_friction_with_charter_attrs_visible_to_filter(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="charter-context protocol defect",
        agent="t",
        charter_root="agent-bus:7001",
        window_index=4,
        actionable=True,
    )
    assert "error" not in result, result
    rows = _op_frictions(charter_root="7001", window_index=4, intent="summary")
    assert "error" not in rows
    items = rows.get("items") or []
    assert any(row["attributes"]["charter_root"] == "7001" for row in items)


@pytest.mark.offline
def test_protocol_actionable_false_escape_without_charter_root(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="informational protocol note",
        agent="t",
        actionable=False,
        actionable_false_reason="no charter context this window",
    )
    assert "error" not in result, result


@pytest.mark.parametrize(
    ("kwargs",),
    [
        ({"charter_root": "5624"},),
        ({"window_index": 2},),
        ({"scoreboard_uri": "cortex://notes/system/threads/scoreboard.md"},),
        ({"checkpoint_turn": 9},),
        ({"charter_root": "5624", "scoreboard_uri": "cortex://sb.md"},),
    ],
)
@pytest.mark.offline
def test_completeness_xor_rejects_partial_charter_stamp(
    friction_db: None,
    kwargs: dict[str, object],
) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="tool_error",
        note="partial charter stamp",
        agent="t",
        **kwargs,
    )
    assert "error" in result
    assert "charter_root and window_index" in result["error"]


@pytest.mark.offline
def test_protocol_continuity_anchor_accepted(friction_db: None) -> None:
    result = _op_friction(
        owner="service:agent-bus",
        category="protocol",
        root_thread="agent-bus:6341",
        cp_ordinal=8,
        actionable=True,
        note="enrollment=none root lacks charter window anchor",
        agent="cursor-sdk",
    )
    assert "error" not in result, result
    item = result.get("item") or {}
    aid = int(item["id"])
    got = _op_assertion_get(assertion_id=aid)
    attrs = got.get("attributes") or {}
    assert attrs.get("root_thread") == "6341"
    assert attrs.get("cp_ordinal") == 8
    assert "charter_root" not in attrs
    assert "window_index" not in attrs
    assert "anchor_kind" not in attrs


@pytest.mark.offline
def test_protocol_mixed_anchor_variant_rejects(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="mixed variants",
        agent="t",
        charter_root="5624",
        cp_ordinal=2,
        actionable=True,
    )
    assert "error" in result
    assert "exactly one complete variant" in result["error"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"root_thread": "6341"},
        {"cp_ordinal": 3},
    ],
)
@pytest.mark.offline
def test_protocol_partial_continuity_rejects(
    friction_db: None,
    kwargs: dict[str, object],
) -> None:
    result = _op_friction(
        owner="service:agent-bus",
        category="protocol",
        note="partial continuity",
        agent="t",
        actionable=True,
        **kwargs,
    )
    assert "error" in result
    assert "exactly one complete variant" in result["error"]


@pytest.mark.parametrize("cp_ordinal", [0, -1, "x"])
@pytest.mark.offline
def test_protocol_invalid_cp_ordinal_rejects(
    friction_db: None,
    cp_ordinal: object,
) -> None:
    result = _op_friction(
        owner="service:agent-bus",
        category="protocol",
        note="bad ordinal",
        agent="t",
        root_thread="6341",
        cp_ordinal=cp_ordinal,  # type: ignore[arg-type]
        actionable=True,
    )
    assert "error" in result


@pytest.mark.offline
def test_protocol_unanchored_actionable_error_names_both_variants(
    friction_db: None,
) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="no anchor",
        agent="t",
        actionable=True,
    )
    assert "error" in result
    assert "charter{charter_root, window_index}" in result["error"]
    assert "continuity{root_thread, cp_ordinal}" in result["error"]
    assert "file_charter_protocol_friction" in result["error"]


@pytest.mark.offline
def test_checkpoint_turn_with_continuity_anchor_accepted(friction_db: None) -> None:
    result = _op_friction(
        owner="service:agent-bus",
        category="protocol",
        note="cp lane pointer",
        agent="t",
        root_thread="6341",
        cp_ordinal=8,
        checkpoint_turn=19,
        actionable=True,
    )
    assert "error" not in result, result


@pytest.mark.offline
def test_checkpoint_turn_alone_rejects_exactly_one_variant(friction_db: None) -> None:
    result = _op_friction(
        owner="service:charter-runner",
        category="tool_error",
        note="orphan checkpoint turn",
        agent="t",
        checkpoint_turn=19,
    )
    assert "error" in result
    assert "exactly one complete variant" in result["error"]
    assert "charter provenance requires" not in result["error"]


@pytest.mark.offline
def test_frictions_full_intent_derives_anchor_view(friction_db: None) -> None:
    _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="charter row",
        agent="t",
        charter_root="5624",
        window_index=3,
    )
    _op_friction(
        owner="service:agent-bus",
        category="protocol",
        note="continuity row",
        agent="t",
        root_thread="6341",
        cp_ordinal=8,
        actionable=True,
    )
    rows = _op_frictions(intent="full", limit=20)
    assert "error" not in rows
    by_kind = {
        row["attributes"]["anchor_kind"]: row["attributes"]
        for row in rows.get("items") or []
        if row.get("attributes", {}).get("anchor_kind") in ("charter", "continuity")
    }
    assert by_kind["charter"]["anchor_root"] == "5624"
    assert by_kind["charter"]["anchor_seq"] == 3
    assert by_kind["continuity"]["anchor_root"] == "6341"
    assert by_kind["continuity"]["anchor_seq"] == 8


@pytest.mark.offline
def test_frictions_anchor_kind_filter_selects_continuity(friction_db: None) -> None:
    _op_friction(
        owner="service:charter-runner",
        category="protocol",
        note="charter",
        agent="t",
        charter_root="5624",
        window_index=1,
    )
    _op_friction(
        owner="service:agent-bus",
        category="protocol",
        note="continuity",
        agent="t",
        root_thread="6341",
        cp_ordinal=8,
        actionable=True,
    )
    rows = _op_frictions(anchor_kind="continuity", intent="full", limit=20)
    items = rows.get("items") or []
    assert len(items) == 1
    assert items[0]["attributes"]["anchor_kind"] == "continuity"
    assert items[0]["attributes"]["anchor_root"] == "6341"


@pytest.mark.offline
def test_reconcile_charter_sweep_excludes_continuity_row(friction_db: None) -> None:
    _op_friction(
        owner="service:agent-bus",
        category="protocol",
        note="continuity only",
        agent="t",
        root_thread="6341",
        cp_ordinal=8,
        actionable=True,
    )
    minted = reconcile_charter_frictions("6341")
    assert minted == []


@pytest.mark.offline
def test_friction_logged_event_carries_anchor_kind(friction_db: None) -> None:
    with patch("cortex_store.dispatch_ops.ops_assertions_friction.record") as record_mock:
        result = _op_friction(
            owner="service:agent-bus",
            category="protocol",
            note="event probe",
            agent="t",
            root_thread="6341",
            cp_ordinal=8,
            actionable=True,
        )
        assert "error" not in result, result
        record_mock.assert_called_once()
        assert record_mock.call_args.kwargs.get("anchor_kind") == "continuity"
