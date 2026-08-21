"""Unit tests for live CSE pager address resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from claude_bundles.cdp_registry.models import Registration

from services.git_integration_worker.cursor_auto.cse_pager_resolve import (
    pager_key_for_job,
    refresh_pager_identity,
    resolve_live_cse_address,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _job(**extra) -> AutoJob:
    fields = dict(
        job_id="j1",
        thread_id="6655",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    fields.update(extra)
    return AutoJob(**fields)


def test_pager_key_for_job_home_lane():
    job = _job(from_agent="cdp-operator-6655-day5i", thread_id="9999")
    assert pager_key_for_job(job) == "6655"


@patch(
    "services.git_integration_worker.cursor_auto.cse_pager_resolve.chat_url_for_registration"
)
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.list_active")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.load_watches")
def test_stale_job_stamp_skipped_when_watch_has_current_url(
    mock_watches, mock_list_active, mock_chat_url
):
    mock_watches.return_value = {
        "6655": {
            "registration_id": "reg-new",
            "chat_url": "https://claude.ai/cowork/cse_new",
        }
    }
    mock_list_active.return_value = [
        MagicMock(registration_id="reg-new"),
    ]
    mock_chat_url.side_effect = lambda rid: {
        "reg-new": "https://claude.ai/cowork/cse_new",
    }.get(rid)

    job = _job(
        cse_chat_url="https://claude.ai/cowork/cse_stale",
        cse_registration_id="reg-stale",
    )
    result = resolve_live_cse_address(job)
    assert result["source"] == "hop_watch"
    assert result["chat_url"] == "https://claude.ai/cowork/cse_new"
    assert result["registration_id"] == "reg-new"


@patch(
    "services.git_integration_worker.cursor_auto.cse_pager_resolve.chat_url_for_registration"
)
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.list_active")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.load_watches")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.load_sessions")
def test_stale_watch_skipped_when_reg_not_listable(
    mock_sessions, mock_watches, mock_list_active, mock_chat_url
):
    mock_watches.return_value = {
        "6655": {
            "registration_id": "reg-dead",
            "chat_url": "https://claude.ai/cowork/cse_dead",
        }
    }
    mock_list_active.return_value = [
        Registration(
            registration_id="reg-live",
            port=9222,
            profile_suffix="p1",
            profile=MagicMock(),
            cdp_url="http://127.0.0.1:9222",
            holder="h",
            purpose="operator-proxy",
            mission_kind="hop",
            parent_thread="6655",
        ),
    ]
    mock_chat_url.side_effect = lambda rid: {
        "reg-live": "https://claude.ai/cowork/cse_live",
    }.get(rid)
    mock_sessions.return_value = {}

    with patch(
        "services.git_integration_worker.cursor_auto.cse_pager_resolve.load_active_rows",
        return_value={"reg-live": {"started_at": 200.0, "purpose": "operator-proxy"}},
    ):
        job = _job()
        result = resolve_live_cse_address(job)
    assert result["source"] == "registry"
    assert result["registration_id"] == "reg-live"


@patch(
    "services.git_integration_worker.cursor_auto.cse_pager_resolve.chat_url_for_registration"
)
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.list_active")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.load_watches")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.load_sessions")
def test_registry_prefers_unique_hop_kind(
    mock_sessions, mock_watches, mock_list_active, mock_chat_url
):
    mock_watches.return_value = {}
    mock_sessions.return_value = {}
    mock_list_active.return_value = [
        Registration(
            registration_id="reg-root",
            port=9222,
            profile_suffix="p1",
            profile=MagicMock(),
            cdp_url="http://127.0.0.1:9222",
            holder="h",
            purpose="operator-proxy",
            mission_kind="root",
            parent_thread="6655",
        ),
        Registration(
            registration_id="reg-hop",
            port=9223,
            profile_suffix="p2",
            profile=MagicMock(),
            cdp_url="http://127.0.0.1:9223",
            holder="h",
            purpose="operator-proxy",
            mission_kind="hop",
            parent_thread="6655",
        ),
    ]
    mock_chat_url.side_effect = lambda rid: {
        "reg-hop": "https://claude.ai/cowork/cse_hop",
        "reg-root": "https://claude.ai/cowork/cse_root",
    }.get(rid)

    with patch(
        "services.git_integration_worker.cursor_auto.cse_pager_resolve.load_active_rows",
        return_value={
            "reg-root": {"started_at": 500.0},
            "reg-hop": {"started_at": 100.0},
        },
    ):
        result = resolve_live_cse_address(_job())
    assert result["source"] == "registry"
    assert result["registration_id"] == "reg-hop"


@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.stamp_session_ids")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.save_watches")
@patch("services.git_integration_worker.cursor_auto.cse_pager_resolve.load_watches")
def test_refresh_pager_identity_preserves_cadence_fields(
    mock_load, mock_save, mock_stamp
):
    mock_load.return_value = {
        "6655": {
            "thread_id": "6655",
            "seated_at": 1.0,
            "last_hop_at": 2.0,
            "registration_id": "reg-old",
        }
    }
    refresh_pager_identity(
        "6655",
        chat_url="https://claude.ai/cowork/cse_new",
        registration_id="reg-new",
    )
    saved = mock_save.call_args[0][0]["6655"]
    assert saved["seated_at"] == 1.0
    assert saved["last_hop_at"] == 2.0
    assert saved["chat_url"] == "https://claude.ai/cowork/cse_new"
    assert saved["registration_id"] == "reg-new"
    mock_stamp.assert_called_once()
