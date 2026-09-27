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
