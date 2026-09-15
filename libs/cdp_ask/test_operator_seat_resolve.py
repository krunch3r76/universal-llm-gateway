"""Tests for cross-host operator CSE identity resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cdp_ask.client import CdpAskClientError
from cdp_ask.operator_seat_resolve import (
    _chat_url_from_provenance,
    resolve_operator_seat,
)

pytestmark = pytest.mark.offline


def _dormant_snap(*, parent_thread: str = "10479") -> dict:
    return {
        "seat_rows": [
            {
                "registration_id": "reg-dormant",
                "parent_thread": parent_thread,
                "purpose": "mission",
                "seat_state": "dormant",
                "mission_kind": "root",
                "started_at": 100.0,
                "chat_url": "https://claude.ai/chat/dormant",
            }
        ]
    }


def test_resolve_dormant_seat_row_via_http() -> None:
    def _snap() -> dict:
        return _dormant_snap()

    out = resolve_operator_seat("10479", get_lane_snapshot=_snap)
    assert out["registration_id"] == "reg-dormant"
    assert out["chat_url"] == "https://claude.ai/chat/dormant"
    assert out["source"] == "http"


def test_http_failure_falls_back_to_local_then_null() -> None:
    def _fail() -> dict:
        raise CdpAskClientError("unreachable")

    with (
        patch("cdp_ask.operator_seat_resolve.list_active", return_value=[]),
        patch("cdp_ask.operator_seat_resolve.load_active", return_value={}),
    ):
        out = resolve_operator_seat("10479", get_lane_snapshot=_fail)
    assert out == {"chat_url": None, "registration_id": None, "source": None}


def test_http_malformed_json_falls_back_without_raise() -> None:
    with (
        patch("cdp_ask.operator_seat_resolve.list_active", return_value=[]),
        patch("cdp_ask.operator_seat_resolve.load_active", return_value={}),
    ):
        out = resolve_operator_seat("10479", get_lane_snapshot=lambda: {})
    assert out["source"] is None


def test_purpose_mismatch_not_resolved() -> None:
    snap = _dormant_snap()
    snap["seat_rows"][0]["purpose"] = "ask"
    out = resolve_operator_seat("10479", get_lane_snapshot=lambda: snap)
    assert out["registration_id"] is None


def test_parent_thread_mismatch_not_resolved() -> None:
    out = resolve_operator_seat("9999", get_lane_snapshot=lambda: _dormant_snap())
    assert out["registration_id"] is None


def test_hop_tie_break_wins_over_newer_root() -> None:
    snap = {
        "seat_rows": [
            {
                "registration_id": "reg-root",
                "parent_thread": "10479",
                "purpose": "operator-proxy",
                "mission_kind": "root",
                "started_at": 500.0,
            },
            {
                "registration_id": "reg-hop",
                "parent_thread": "10479",
                "purpose": "operator-proxy",
                "mission_kind": "hop",
                "started_at": 100.0,
                "chat_url": "https://claude.ai/chat/hop",
            },
        ]
    }
    out = resolve_operator_seat("10479", get_lane_snapshot=lambda: snap)
    assert out["registration_id"] == "reg-hop"
    assert out["source"] == "http"


def test_local_fallback_when_http_empty() -> None:
    reg = MagicMock()
    reg.registration_id = "reg-local"
    reg.parent_thread = "10479"
    reg.purpose = "operator-proxy"
    reg.mission_kind = "root"

    with (
        patch(
            "cdp_ask.operator_seat_resolve.list_active",
            return_value=[reg],
        ),
        patch(
            "cdp_ask.operator_seat_resolve.load_active",
            return_value={"reg-local": {"started_at": 42.0}},
        ),
        patch(
            "cdp_ask.operator_seat_resolve.chat_url_for_registration",
            return_value="https://claude.ai/chat/local",
        ),
    ):
        out = resolve_operator_seat(
            "10479",
            get_lane_snapshot=lambda: {"seat_rows": []},
        )
    assert out["registration_id"] == "reg-local"
    assert out["source"] == "local"


def _snap_no_chat_url(*, parent_thread: str = "10479") -> dict:
    return {
        "seat_rows": [
            {
                "registration_id": "reg-remote",
                "parent_thread": parent_thread,
                "purpose": "mission",
                "seat_state": "dormant",
                "mission_kind": "root",
                "started_at": 100.0,
            }
        ]
    }


def test_http_path_enriches_chat_url_from_provenance() -> None:
    with patch(
        "cdp_ask.operator_seat_resolve._chat_url_from_provenance",
        return_value="https://claude.ai/chat/provenance",
    ):
        out = resolve_operator_seat(
            "10479",
            get_lane_snapshot=lambda: _snap_no_chat_url(),
        )
    assert out == {
        "chat_url": "https://claude.ai/chat/provenance",
        "registration_id": "reg-remote",
        "source": "http",
    }


@pytest.mark.parametrize(
    ("side_effect", "return_value"),
    [
        (CdpAskClientError("404", status_code=404), None),
        (CdpAskClientError("unreachable"), None),
        (None, None),
        (None, {"registration_id": "reg-remote"}),
        (None, "not-a-dict"),
    ],
)
def test_provenance_misses_degrade_without_raise(
    side_effect: Exception | None,
    return_value: object,
) -> None:
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client._request.side_effect = side_effect
    else:
        mock_client._request.return_value = return_value
    with patch(
        "cdp_ask.operator_seat_resolve.CdpAskClient",
        return_value=mock_client,
    ):
        assert _chat_url_from_provenance("reg-remote") is None


def test_provenance_identity_mismatch_yields_no_chat_url() -> None:
    mock_client = MagicMock()
    mock_client._request.return_value = {
        "registration_id": "foreign-reg",
        "chat_url": "https://claude.ai/chat/foreign",
    }
    with patch(
        "cdp_ask.operator_seat_resolve.CdpAskClient",
        return_value=mock_client,
    ):
        assert _chat_url_from_provenance("reg-requested") is None


def test_http_provenance_failure_falls_back_to_null_chat_url() -> None:
    with (
        patch(
            "cdp_ask.operator_seat_resolve._chat_url_from_provenance",
            return_value=None,
        ),
        patch(
            "cdp_ask.operator_seat_resolve.chat_url_for_registration",
            return_value=None,
        ),
    ):
        out = resolve_operator_seat(
            "10479",
            get_lane_snapshot=lambda: _snap_no_chat_url(),
        )
    assert out["registration_id"] == "reg-remote"
    assert out["chat_url"] is None
    assert out["source"] == "http"
