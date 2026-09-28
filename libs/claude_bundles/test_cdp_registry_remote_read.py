"""read_fleet_registry — HTTP document reader for hub."""

from __future__ import annotations

import http.client
from unittest.mock import patch

import pytest

from claude_bundles.cdp_registry_remote_read import (
    _reset_fleet_registry_cache_for_tests,
    read_fleet_registry,
)


@pytest.mark.offline
def test_read_fleet_registry_never_returns_empty_dict() -> None:
    _reset_fleet_registry_cache_for_tests()
    with patch(
        "claude_bundles.cdp_registry_remote_read._fetch_registry_document",
        side_effect=OSError("down"),
    ):
        doc = read_fleet_registry(force_refresh=True)
    assert doc != {}
    assert doc["availability"] == "unavailable"
    assert doc["seat_count"] is None


@pytest.mark.offline
def test_read_fleet_registry_http_exception_becomes_unavailable() -> None:
    _reset_fleet_registry_cache_for_tests()
    with patch(
        "claude_bundles.cdp_registry_remote_read._fetch_registry_document",
        side_effect=http.client.HTTPException("bad status line"),
    ):
        doc = read_fleet_registry(force_refresh=True)
    assert doc["availability"] == "unavailable"
    assert doc["seat_count"] is None
    assert doc["seats"] is None


@pytest.mark.offline
def test_read_fleet_registry_value_error_becomes_unavailable() -> None:
    _reset_fleet_registry_cache_for_tests()
    with patch(
        "claude_bundles.cdp_registry_remote_read._fetch_registry_document",
        side_effect=ValueError("invalid port"),
    ):
        doc = read_fleet_registry(force_refresh=True)
    assert doc["availability"] == "unavailable"
    assert doc["seat_count"] is None
    assert doc["seats"] is None


@pytest.mark.offline
def test_read_fleet_registry_ok_shape() -> None:
    _reset_fleet_registry_cache_for_tests()
    payload = {
        "availability": "ok",
        "seat_count": 1,
        "seats": [{"registration_id": "r1", "status": "active"}],
    }
    with patch(
        "claude_bundles.cdp_registry_remote_read._fetch_registry_document",
        return_value=payload,
    ):
        doc = read_fleet_registry(force_refresh=True)
    assert doc["availability"] == "ok"
    assert doc["seat_count"] == 1


@pytest.mark.offline
def test_read_fleet_registry_does_not_call_load_active() -> None:
    _reset_fleet_registry_cache_for_tests()
    with patch(
        "claude_bundles.cdp_registry_store.load_active",
        side_effect=AssertionError("load_active must not run"),
    ):
        with patch(
            "claude_bundles.cdp_registry_remote_read._fetch_registry_document",
            return_value={"availability": "unavailable", "seats": None, "seat_count": None},
        ):
            read_fleet_registry(force_refresh=True)


@pytest.mark.offline
def test_attach_unavailable_does_not_inject_empty_lists() -> None:
    from claude_bundles.hop_cadence_seat_snap import attach_registry_seated_rows

    snap = {"rows": [], "running_count": 0}
    with patch(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        return_value={"availability": "unavailable", "seats": None, "seat_count": None},
    ):
        out = attach_registry_seated_rows(snap)
    assert "seated_rows" not in out
    assert "seat_rows" not in out
    assert out.get("registry_availability") == "unavailable"
