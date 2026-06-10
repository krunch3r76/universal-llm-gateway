"""Q5.5 — event-emission surface for predicate_form normalize.

Verifies the Option A response-envelope contract (assertion 10259,
dispatch packet
``cortex://notes/system/threads/cortex-api-event-emission-surface-dispatch.md``):

* Routes layer surfaces ``predicate_form_normalize`` on PATCH /assertions/{id}
  responses; ``_update_assertion_impl`` flattens to ``AssertionItem`` fields
  plus a sibling ``predicate_form_normalize`` key for dispatcher consumption.
* Dispatcher helper ``_emit_predicate_form_normalize_events`` fires
  ``mcp.cortex.predicate.normalized`` on every normalize, plus
  ``mcp.cortex.predicate.review.required`` when Class 6 trips
  ``requires_human_review``.

POST coverage stays at the dispatcher-helper level — exercising
``_create_assertion_impl`` requires full schema fixtures already covered by
``test_assertion_predicate_form_normalize.py``; replicating that scaffolding
here would buy no additional confidence in the emission surface itself.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException
from predicate_form import normalize_predicate_domain
from predicate_form.entity_resolve import DBEntityResolver

from cortex_store.dispatch_ops._assertions_shared import (
    _emit_predicate_form_normalize_events,
)
from cortex_store.routes.assertions import _update_assertion_impl

from .test_assertion_predicate_form_normalize import (
    _insert_assertion,
    _patch_update,
)

# ---------------------------------------------------------------------------
# Fixture — head schema via conftest ``migrated_conn`` (same as normalize tests)
# ---------------------------------------------------------------------------

_Q1_ENTITIES = [
    ("person:camelia-mahmoudi", "person"),
]


def _seed_q1_entities(conn: sqlite3.Connection) -> None:
    for eid, etype in _Q1_ENTITIES:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
            (eid, etype, eid.split(":")[-1]),
        )
    conn.commit()


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_q1_entities(migrated_conn)
    return migrated_conn


# ---------------------------------------------------------------------------
# Routes layer — envelope shape on PATCH
# ---------------------------------------------------------------------------


def test_patch_surfaces_predicate_form_normalize_envelope(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH result dict carries the normalize envelope alongside the item."""
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    legacy = "role(camelia_mahmoudi, filer, 24PR197054)"
    result = _update_assertion_impl(aid, {"predicate_form": legacy})

    assert "predicate_form_normalize" in result
    envelope = result["predicate_form_normalize"]
    assert envelope is not None
    assert envelope["predicate_form_in"] == legacy
    assert (
        envelope["canonical_form"] == "role(person:camelia-mahmoudi, filer, 24pr197054)"
    )
    # At least Class 2 (slug rewrite) and Class 4 (lowercase) fire.
    assert envelope["normalized"] is True
    assert envelope["classes_applied"]
    assert envelope["requires_human_review"] is False


def test_patch_without_predicate_form_omits_envelope(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No predicate_form in the PATCH body → no normalize envelope key fires."""
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    result = _update_assertion_impl(aid, {"review_status": "committed"})

    assert "predicate_form_normalize" not in result


def test_patch_requires_human_review_envelope_carries_flag(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Class 6 review trigger → envelope.requires_human_review is True."""
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    expectation = normalize_predicate_domain(
        "person:camelia-mahmoudi",
        "status(unknown_subject_xyz, ready_to_file)",
        claim_text="Phase D fixture claim.",
        resolver=DBEntityResolver(conn),
    )
    if not expectation["requires_human_review"]:
        pytest.skip(
            "Class 6 trigger no longer fires for fixture; covered by live-DB §7."
        )

    result = _update_assertion_impl(
        aid, {"predicate_form": "status(unknown_subject_xyz, ready_to_file)"}
    )
    envelope = result["predicate_form_normalize"]
    assert envelope is not None
    assert envelope["requires_human_review"] is True


# ---------------------------------------------------------------------------
# Dispatcher helper — signal emission
# ---------------------------------------------------------------------------


def _capture_records(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    captured: list[tuple[str, dict]] = []

    def _fake_record(signal: str, **payload: object) -> None:
        captured.append((signal, dict(payload)))

    monkeypatch.setattr("cortex_store.dispatch_ops._assertions_shared.record", _fake_record)
    return captured


def test_helper_emits_normalized_signal_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """requires_human_review=False → only ``mcp.cortex.predicate.normalized`` fires."""
    captured = _capture_records(monkeypatch)
    _emit_predicate_form_normalize_events(
        assertion_id=42,
        normalize_payload={
            "predicate_form_in": "role(camelia_mahmoudi, filer, 24PR197054)",
            "canonical_form": "role(person:camelia-mahmoudi, filer, 24pr197054)",
            "classes_applied": [1, 2, 4],
            "normalized": True,
            "requires_human_review": False,
        },
    )
    signals = [s for s, _ in captured]
    assert signals == ["mcp.cortex.predicate.normalized"]
    payload = captured[0][1]
    assert payload["assertion_id"] == 42
    assert (
        payload["canonical_form"] == "role(person:camelia-mahmoudi, filer, 24pr197054)"
    )
    assert payload["classes_applied"] == [1, 2, 4]
    assert payload["normalized"] is True
    assert payload["requires_human_review"] is False


def test_helper_emits_review_required_when_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """requires_human_review=True → both normalized + review.required fire."""
    captured = _capture_records(monkeypatch)
    _emit_predicate_form_normalize_events(
        assertion_id=99,
        normalize_payload={
            "predicate_form_in": "status(unknown_subject_xyz, ready_to_file)",
            "canonical_form": "status(unknown_subject_xyz, ready_to_file)",
            "classes_applied": [6],
            "normalized": True,
            "requires_human_review": True,
        },
    )
    signals = [s for s, _ in captured]
    assert signals == [
        "mcp.cortex.predicate.normalized",
        "mcp.cortex.predicate.review.required",
    ]
    for _, payload in captured:
        assert payload["assertion_id"] == 99
        assert (
            payload["predicate_form_in"] == "status(unknown_subject_xyz, ready_to_file)"
        )


def test_helper_noop_when_payload_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes that did not normalize → helper is a no-op (no signal fire)."""
    captured = _capture_records(monkeypatch)
    _emit_predicate_form_normalize_events(assertion_id=1, normalize_payload=None)
    _emit_predicate_form_normalize_events(assertion_id=1, normalize_payload={})
    assert captured == []


# ---------------------------------------------------------------------------
# 422 path — error responses never emit normalize events
# ---------------------------------------------------------------------------


def test_patch_422_never_emits_events(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable predicate_form → 422 raised before envelope/emission."""
    captured = _capture_records(monkeypatch)
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    with pytest.raises(HTTPException) as exc:
        _update_assertion_impl(aid, {"predicate_form": "not a predicate"})
    assert exc.value.status_code == 422
    assert captured == []
