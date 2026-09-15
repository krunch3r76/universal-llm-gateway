"""CSE follow-up transport for liaison induction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from bus_watch.cse_followup import fire_cse_followup, resolve_cse_identity

pytestmark = pytest.mark.offline


def _mock_reg(
    *,
    registration_id: str = "reg-10479",
    parent_thread: str = "10479",
    purpose: str = "operator-proxy",
    mission_kind: str = "root",
    chat_url: str | None = "https://claude.ai/chat/abc",
) -> MagicMock:
    reg = MagicMock()
    reg.registration_id = registration_id
    reg.parent_thread = parent_thread
    reg.purpose = purpose
    reg.mission_kind = mission_kind
    return reg


def test_resolve_cse_identity_by_parent_thread() -> None:
    reg = _mock_reg()
    with (
        patch("bus_watch.cse_followup.list_active", return_value=[reg]),
        patch(
            "bus_watch.cse_followup.load_active",
            return_value={reg.registration_id: {"started_at": 100.0}},
        ),
        patch(
            "bus_watch.cse_followup.chat_url_for_registration",
            return_value="https://claude.ai/chat/abc",
        ),
    ):
        out = resolve_cse_identity("10479")
    assert out["registration_id"] == "reg-10479"
    assert out["url"] == "https://claude.ai/chat/abc"


def test_fire_cse_followup_dry_run() -> None:
    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/chat/abc",
                "registration_id": "reg-10479",
                "url": "https://claude.ai/chat/abc",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup("WAKE 10479", "10479", dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["body"]["prompt_text"] == "WAKE 10479"
    assert out["body"]["reattach"] is True
    assert out["body"]["chat_url"] == "https://claude.ai/chat/abc"


def test_fire_cse_followup_posts_followups() -> None:
    calls: list[dict] = []

    def _post(method: str, url: str, *, json=None, timeout: float):  # noqa: ANN001
        calls.append({"method": method, "url": url, "json": json, "timeout": timeout})
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.content = (
            b'{"ok": true, "send_verified": true, "url": "https://claude.ai/chat/abc"}'
        )
        resp.json.return_value = {
            "ok": True,
            "send_verified": True,
            "url": "https://claude.ai/chat/abc",
            "registration_id": "reg-10479",
        }
        return resp

    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/chat/abc",
                "registration_id": "reg-10479",
                "url": "https://claude.ai/chat/abc",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup("WAKE 10479", "10479", post=_post)
    assert out["ok"] is True
    assert out["send_verified"] is True
    assert out["url"] == "https://claude.ai/chat/abc"
    assert len(calls) == 1
    assert calls[0]["url"] == "http://127.0.0.1:9191/v1/project-ask/followups"
    assert calls[0]["json"]["reattach"] is True


def test_fire_cse_followup_no_identity() -> None:
    with patch(
        "bus_watch.cse_followup.resolve_cse_identity",
        return_value={"chat_url": None, "registration_id": None, "url": None},
    ):
        out = fire_cse_followup("WAKE 10479", "10479")
    assert out["ok"] is False
    assert out["error"] == "no_identity"


def test_fire_cse_followup_transport_error_never_raises() -> None:
    def _post(method: str, url: str, *, json=None, timeout: float):  # noqa: ANN001
        raise httpx.ConnectError("connection refused")

    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/chat/abc",
                "registration_id": "reg-10479",
                "url": "https://claude.ai/chat/abc",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup("WAKE 10479", "10479", post=_post)
    assert out["ok"] is False
    assert "unreachable" in out["error"]
