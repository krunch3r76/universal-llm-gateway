"""Tests for cross-host operator CSE identity resolution."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest
from claude_bundles.cdp_registry.models import seat_open
from claude_bundles.cdp_registry.session_address import apply_driving_seat_bind
from claude_bundles.hop_cadence_seat_snap import (
    seat_row_from_registry_record,
    seat_rows_from_registry_records,
)

from cdp_ask.client import CdpAskClientError
from cdp_ask.operator_seat_resolve import (
    _candidates_from_seat_rows,
    _chat_url_from_provenance,
    _resolve_from_http,
    _resolve_from_local,
    _select_registration_id,
    resolve_operator_seat,
)

pytestmark = pytest.mark.offline

_RESOLVER_READ_FIELDS = frozenset(
    {"registration_id", "parent_thread", "purpose", "seat_bound_at"}
)


def _registry_record(**overrides: object) -> dict:
    base = {
        "registration_id": "reg-dormant",
        "status": "dormant",
        "purpose": "operator-proxy",
        "parent_thread": "10479",
        "seat_lane": "10479",
        "seat_bound_at": 100.0,
        "execution_id": "",
    }
    base.update(overrides)
    return base


def test_seat_row_carries_every_field_the_resolver_reads() -> None:
    projected = seat_row_from_registry_record(_registry_record())
    assert projected is not None
    assert _RESOLVER_READ_FIELDS <= set(projected.keys())
    for field in _RESOLVER_READ_FIELDS:
        assert projected[field] is not None or field == "execution_id"


def test_seat_row_projection_field_set() -> None:
    projected = seat_row_from_registry_record(_registry_record())
    assert projected is not None
    assert set(projected.keys()) == {
        "registration_id",
        "execution_id",
        "parent_thread",
        "purpose",
        "seat_state",
        "stream_state",
        "source",
        "seat",
        "host_status",
        "seat_lane",
        "seat_bound_at",
    }
    assert "chat_url" not in projected
    assert "cdp_url" not in projected
    assert "port" not in projected


def test_hop_row_is_never_seat_open() -> None:
    active = {
        "reg-hop": _registry_record(
            mission_kind="hop",
            seat_lane=None,
            seat_bound_at=None,
        )
    }
    bound, _released = apply_driving_seat_bind(active, "reg-hop")
    assert bound is None
    row = active["reg-hop"]
    assert seat_open(row) is False
    assert seat_row_from_registry_record(row) is None


def test_select_registration_id_picks_max_seat_bound_at_with_reason() -> None:
    reg_id, reason = _select_registration_id(
        [
            ("reg-old", 10.0),
            ("reg-new", 500.0),
        ]
    )
    assert reg_id == "reg-new"
    assert reason is not None
    assert "seat_bound_at" in reason
    assert "reg-new" in reason


def test_resolve_dormant_seat_row_via_http() -> None:
    seat_rows = seat_rows_from_registry_records([_registry_record()])
    snap = {"seat_rows": seat_rows}
    with patch(
        "cdp_ask.operator_seat_resolve._chat_url_from_provenance",
        return_value="https://claude.ai/cowork/cse_dormant",
    ):
        out = resolve_operator_seat("10479", get_lane_snapshot=lambda: snap)
    assert out["registration_id"] == "reg-dormant"
    assert out["chat_url"] == "https://claude.ai/cowork/cse_dormant"
    assert out["source"] == "http"


def test_http_failure_falls_back_to_local_then_null() -> None:
    def _fail() -> dict:
        raise CdpAskClientError("unreachable")

    with patch("cdp_ask.operator_seat_resolve.load_active", return_value={}):
        out = resolve_operator_seat("10479", get_lane_snapshot=_fail)
    assert out == {"chat_url": None, "registration_id": None, "source": None}


def test_http_malformed_json_falls_back_without_raise() -> None:
    with patch("cdp_ask.operator_seat_resolve.load_active", return_value={}):
        out = resolve_operator_seat("10479", get_lane_snapshot=lambda: {})
    assert out["source"] is None


def test_purpose_mismatch_not_resolved() -> None:
    record = _registry_record(purpose="ask")
    snap = {"seat_rows": seat_rows_from_registry_records([record])}
    out = resolve_operator_seat("10479", get_lane_snapshot=lambda: snap)
    assert out["registration_id"] is None


def test_parent_thread_mismatch_not_resolved() -> None:
    record = _registry_record(parent_thread="9999", seat_lane="9999")
    snap = {"seat_rows": seat_rows_from_registry_records([record])}
    out = resolve_operator_seat("10479", get_lane_snapshot=lambda: snap)
    assert out["registration_id"] is None


def test_two_seat_open_rows_selects_newest_bound_at() -> None:
    records = [
        _registry_record(
            registration_id="reg-old",
            seat_bound_at=100.0,
        ),
        _registry_record(
            registration_id="reg-new",
            seat_bound_at=500.0,
        ),
    ]
    seat_rows = seat_rows_from_registry_records(records)
    candidates = _candidates_from_seat_rows(seat_rows, "10479", frozenset({"operator-proxy"}))
    reg_id, reason = _select_registration_id(candidates)
    assert reg_id == "reg-new"
    assert reason is not None
    assert "500.0" in reason


def test_local_fallback_when_http_empty() -> None:
    record = _registry_record(
        registration_id="reg-local",
        status="active",
        seat_bound_at=42.0,
    )
    with (
        patch(
            "cdp_ask.operator_seat_resolve.load_active",
            return_value={"reg-local": record},
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


@pytest.mark.parametrize(
    "records",
    [
        [_registry_record(status="dormant", seat_bound_at=200.0)],
        [
            _registry_record(
                registration_id="reg-open",
                status="active",
                seat_bound_at=300.0,
            ),
            _registry_record(
                registration_id="reg-closed",
                status="released",
                seat_bound_at=100.0,
                seat_closed_at=50.0,
            ),
        ],
        [
            _registry_record(
                registration_id="reg-old",
                status="active",
                seat_bound_at=100.0,
            ),
            _registry_record(
                registration_id="reg-new",
                status="dormant",
                seat_bound_at=500.0,
            ),
        ],
    ],
)
def test_local_http_parity_on_seat_rows(records: list[dict]) -> None:
    """AC3: local and HTTP paths agree on registration_id for the same map."""
    active = {str(r["registration_id"]): dict(r) for r in records}
    seat_rows = seat_rows_from_registry_records(active)
    snap = {"seat_rows": seat_rows}
    purposes = frozenset({"operator-proxy"})

    with (
        patch(
            "cdp_ask.operator_seat_resolve.load_active",
            return_value=active,
        ),
        patch(
            "cdp_ask.operator_seat_resolve._chat_url_from_provenance",
            return_value=None,
        ),
        patch(
            "cdp_ask.operator_seat_resolve.chat_url_for_registration",
            return_value=None,
        ),
    ):
        local = _resolve_from_local("10479", purposes)
        http = _resolve_from_http(
            "10479",
            purposes,
            get_lane_snapshot=lambda: snap,
        )
    assert local["registration_id"] == http["registration_id"]


def test_http_path_enriches_chat_url_from_provenance() -> None:
    seat_rows = seat_rows_from_registry_records([_registry_record(registration_id="reg-remote")])
    with patch(
        "cdp_ask.operator_seat_resolve._chat_url_from_provenance",
        return_value="https://claude.ai/chat/provenance",
    ):
        out = resolve_operator_seat(
            "10479",
            get_lane_snapshot=lambda: {"seat_rows": seat_rows},
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
    seat_rows = seat_rows_from_registry_records([_registry_record(registration_id="reg-remote")])
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
            get_lane_snapshot=lambda: {"seat_rows": seat_rows},
        )
    assert out["registration_id"] == "reg-remote"
    assert out["chat_url"] is None
    assert out["source"] == "http"


def test_resolver_read_set_has_no_defaults_in_projection() -> None:
    source = inspect.getsource(_candidates_from_seat_rows)
    projected = seat_row_from_registry_record(_registry_record(seat_bound_at=123.0))
    assert projected is not None
    for field in _RESOLVER_READ_FIELDS:
        assert field in source
        if field == "seat_bound_at":
            assert projected[field] == 123.0
