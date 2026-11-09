"""CSE follow-up transport for liaison induction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    with patch(
        "bus_watch.cse_followup.resolve_operator_seat",
        return_value={
            "chat_url": "https://claude.ai/chat/abc",
            "registration_id": "reg-10479",
            "source": "local",
        },
    ):
        out = resolve_cse_identity("10479")
    assert out["registration_id"] == "reg-10479"
    assert out["url"] == "https://claude.ai/chat/abc"
    assert out["source"] == "local"


def test_resolve_cse_identity_delegates_http_source() -> None:
    with patch(
        "bus_watch.cse_followup.resolve_operator_seat",
        return_value={
            "chat_url": "https://claude.ai/chat/dormant",
            "registration_id": "reg-dormant",
            "source": "http",
        },
    ):
        out = resolve_cse_identity("10479")
    assert out["source"] == "http"
    assert out["registration_id"] == "reg-dormant"


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
        return_value={
            "chat_url": None,
            "registration_id": None,
            "url": None,
            "source": "local",
        },
    ):
        out = fire_cse_followup("WAKE 10479", "10479")
    assert out["ok"] is False
    assert out["error"] == "no_identity"
    assert out["source"] == "local"


def test_fire_cse_followup_giw_fallback_populates_chat_url() -> None:
    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": None,
                "registration_id": None,
                "url": None,
                "source": "local",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup(
            "wake turns 3",
            "12286",
            dry_run=True,
            fallback_chat_url="https://claude.ai/chat/streaming",
            fallback_registration_id="reg-giw",
        )
    assert out["ok"] is True
    assert out["body"]["chat_url"] == "https://claude.ai/chat/streaming"
    assert out["body"]["registration_id"] == "reg-giw"


def test_fire_cse_followup_response_registration_overrides_request() -> None:
    def _post(method: str, url: str, *, json=None, timeout: float):  # noqa: ANN001
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {
            "ok": True,
            "registration_id": "reg-reattached",
            "url": "https://claude.ai/cowork/cse_new",
        }
        return resp

    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/cowork/cse_old",
                "registration_id": "reg-10479",
                "url": "https://claude.ai/cowork/cse_old",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup("WAKE 10479", "10479", post=_post)
    assert out["registration_id"] == "reg-reattached"
    assert out["url"] == "https://claude.ai/cowork/cse_new"


def test_fire_cse_followup_houseless_omits_operator_purpose() -> None:
    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/cowork/cse_houseless",
                "registration_id": "reg-houseless",
                "url": "https://claude.ai/cowork/cse_houseless",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup(
            "wake turns 2",
            "22760",
            dry_run=True,
            purpose=None,
            reattach=False,
        )
    assert out["ok"] is True
    assert "purpose" not in out["body"]
    assert out["body"]["reattach"] is False


@pytest.mark.asyncio
async def test_execute_followup_houseless_no_reattach_never_registers_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.execution_store import ExecutionStore
    from cdp_ask.followup import execute_followup
    from cdp_ask.models import FollowupProjectAskRequest

    reg = "583b411df34247f9a48bc044b6518be3"
    mint_calls: list[dict] = []

    def _forbidden_register_lane(*_a: object, **_k: object) -> object:
        mint_calls.append(dict(_k))
        raise AssertionError("register_lane must not run for houseless delivery")

    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {
            reg: {
                "registration_id": reg,
                "purpose": "houseless",
                "status": "active",
            }
        },
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.register_lane",
        _forbidden_register_lane,
    )

    def _forbidden_reattach(*_a: object, **_k: object) -> object:
        raise AssertionError("ensure_cse_attached must not run")

    monkeypatch.setattr(
        "cdp_ask.followup.ensure_cse_attached",
        _forbidden_reattach,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )

    async def _resolve_target(req, store, **kwargs):  # type: ignore[no-untyped-def]
        from cdp_ask.models import FollowupProjectAskResponse

        return (
            None,
            FollowupProjectAskResponse(ok=False, error="no_active_target"),
            "test",
            None,
        )

    monkeypatch.setattr("cdp_ask.followup.resolve_followup_target", _resolve_target)
    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="wake turns 2",
            parent_thread="22760",
            chat_url="https://claude.ai/cowork/cse_closed",
            registration_id=reg,
            purpose=None,
            reattach=False,
        ),
        ExecutionStore(),
    )
    assert resp.ok is False
    assert resp.error == "no_active_target"
    assert resp.error != "houseless_no_attach"
    assert not mint_calls


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
