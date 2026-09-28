"""Registry document builder — vocabulary seat_count."""

from __future__ import annotations

import pytest

from claude_bundles.cdp_registry_document import build_registry_document


@pytest.mark.offline
def test_seat_count_ignores_blank_and_unknown_status() -> None:
    active = {
        "a": {"registration_id": "a", "status": "active", "started_at": 1.0},
        "b": {"registration_id": "b", "status": ""},
        "c": {"registration_id": "c", "status": "not-a-vocab-status"},
        "d": {"registration_id": "d", "status": "dormant"},
    }
    doc = build_registry_document(active)
    assert doc["availability"] == "ok"
    assert doc["seat_count"] == 2
    assert doc["blank_status_omitted"] == 1
    assert doc["unknown_status_omitted"] == 1
    assert len(active) == 4
    assert doc["seat_count"] != len(active)


@pytest.mark.offline
def test_seats_sorted_by_registration_id() -> None:
    active = {
        "z": {"registration_id": "z", "status": "active"},
        "a": {"registration_id": "a", "status": "retained"},
    }
    doc = build_registry_document(active)
    ids = [row["registration_id"] for row in doc["seats"]]
    assert ids == ["a", "z"]


@pytest.mark.offline
def test_document_emits_seat_axis_fields() -> None:
    active = {
        "r1": {
            "registration_id": "r1",
            "status": "active",
            "execution_id": "exec-1",
            "seat_lane": "10479",
            "seat_bound_at": 1_700_000_000.0,
            "seat_closed_at": None,
        },
    }
    doc = build_registry_document(active)
    assert doc["seat_field_schema"] == 1
    seat = doc["seats"][0]
    assert seat["execution_id"] == "exec-1"
    assert seat["seat_lane"] == "10479"
    assert seat["seat_bound_at"] is not None
    assert seat["seat_closed_at"] is None
