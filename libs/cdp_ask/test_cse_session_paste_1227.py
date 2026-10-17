"""AMEND 15851#1227 stand-down tests for paste and followup detach.

Paste of TYPE: SEAT_STAND_DOWN stamps a token and does not detach.
A followup stand-down leaves that token so a later hop detach succeeds.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.cse_session_models import PasteRequest
from cdp_ask.cse_session_paste import _IDEMPOTENCY, execute_paste
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import FollowupProjectAskResponse


@pytest.fixture(autouse=True)
def _clear_idempotency() -> None:
    _IDEMPOTENCY.clear()
    yield
    _IDEMPOTENCY.clear()


_PRED_39528 = "4f3e647918c64ab18f8c3f9403d24999"
_SUCC_39528 = "ecd5b3dd016945019acbe0d2b4094348"
_BIRTH_39528 = "6348c305"
_CHAT_39528 = "https://claude.ai/cowork/cse_pred_39528"


@pytest.mark.asyncio
async def test_stand_down_paste_stamps_token_without_detaching() -> None:
    """TYPE: SEAT_STAND_DOWN stamps a token and does not detach."""
    logs: list[tuple[str, dict]] = []
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_committed",
        registration_id="pred-reg",
        url=_CHAT_39528,
    )
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={
                "state": "current",
                "registration_id": "pred-reg",
                "chat_url": _CHAT_39528,
                "parent_thread_proven": "12286",
            },
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ),
        patch(
            "claude_bundles.cdp_registry_store.append_log",
            side_effect=lambda event, record: logs.append((event, record)),
        ),
        patch(
            "claude_bundles.cdp_registry.attachment_journal.fold_attachment_journal",
        ) as fold,
        patch("claude_bundles.cdp_registry.detach") as detach,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="pred-reg",
                chat_url=_CHAT_39528,
                grant="explicit",
                prompt_text="TYPE: SEAT_STAND_DOWN\nretire the predecessor seat",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    detach.assert_not_called()
    assert any(
        event == "standdown_pasted" and record.get("registration_id") == "pred-reg"
        for event, record in logs
    )
    fold.assert_called()


@pytest.mark.asyncio
async def test_followup_stand_down_stamps_token_and_allows_detach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12286#3980: followup envelope=stand_down, then the predecessor detaches."""
    import importlib
    import sys

    mcp_server = Path(__file__).resolve().parents[2] / "services" / "mcp-server"
    if str(mcp_server) not in sys.path:
        sys.path.insert(0, str(mcp_server))
    for key in list(sys.modules):
        if key == "tools" or key.startswith("tools."):
            del sys.modules[key]
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite:8770")
    import tools.cse_session as session_mod
    import tools.cse_session_warm as warm_mod

    importlib.reload(warm_mod)
    session_mod = importlib.reload(session_mod)
    relay = MagicMock(return_value={"ok": True, "send_verified": True})
    monkeypatch.setattr(warm_mod, "_relay", relay)
    monkeypatch.setattr(warm_mod, "record", lambda *_a, **_k: None)
    captured: list = []

    class _FakeMcp:
        def tool(self, **_kwargs):
            def _decorator(fn):
                captured.append(fn)
                return fn

            return _decorator

    session_mod.register_cse_session_tool(_FakeMcp())
    captured[0](
        op="followup",
        registration_id=_PRED_39528,
        predecessor_registration_id=_PRED_39528,
        successor_registration_id=_SUCC_39528,
        successor_birth_id=_BIRTH_39528,
        envelope="stand_down",
        prompt_text="TYPE: SEAT_STAND_DOWN",
        min_receipt="dom_committed",
    )
    body = relay.call_args.kwargs["json_body"]
    assert body["envelope"] == "stand_down"
    assert body["predecessor_registration_id"] == _PRED_39528
    assert body["successor_registration_id"] == _SUCC_39528
    assert body["successor_birth_id"] == _BIRTH_39528

    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    from cdp_ask.followup import execute_followup
    from cdp_ask.followup_envelope import FollowupCandidate
    from cdp_ask.models import FollowupProjectAskRequest

    root = tmp_path / "cdp-registry"
    regs = root / "registrations"
    regs.mkdir(parents=True)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: False)
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.release_holder_remote",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "claude_bundles.cse_provenance.append_episode",
        lambda **_k: None,
    )
    reg._store.write_active(
        {
            _PRED_39528: {
                "registration_id": _PRED_39528,
                "port": 9333,
                "profile_suffix": "pred-39528",
                "holder": "pred",
                "status": "active",
                "purpose": "operator-proxy",
                "parent_thread": "12286",
                "seat_lane": "12286",
                "seat_closed_at": 100.0,
                "seat_bound_at": 90.0,
                "started_at": 90.0,
                "chat_url": _CHAT_39528,
                "superseded_by": _SUCC_39528,
            }
        }
    )
    page = MagicMock()
    page.url = _CHAT_39528
    pw = MagicMock()
    pw.stop = AsyncMock()
    candidate = FollowupCandidate(
        registration_id=_PRED_39528,
        chat_url=_CHAT_39528,
        holder="pred",
        purpose="operator-proxy",
        cdp_url="http://127.0.0.1:9333",
    )
    with (
        patch(
            "cdp_ask.followup.resolve_followup_prompt",
            return_value="TYPE: SEAT_STAND_DOWN\nretire predecessor",
        ),
        patch(
            "cdp_ask.followup.resolve_followup_target",
            AsyncMock(return_value=(candidate, None, "registration_id", "explicit")),
        ),
        patch(
            "cdp_ask.followup.find_page_on_lane",
            AsyncMock(return_value=(page, pw)),
        ),
        patch(
            "cdp_ask.followup.send_followup_paste_half",
            AsyncMock(
                return_value={
                    "receipt": "dom_committed",
                    "url": _CHAT_39528,
                    "pasted_at": 1792224619,
                    "streaming_at_paste": True,
                }
            ),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _event: None),
        patch("cdp_ask.followup._stamp_followup_in_flight", lambda *_a, **_k: None),
    ):
        result = await execute_followup(
            FollowupProjectAskRequest(
                registration_id=_PRED_39528,
                chat_url=_CHAT_39528,
                prompt_text="TYPE: SEAT_STAND_DOWN\nretire predecessor",
                envelope="stand_down",
                predecessor_registration_id=_PRED_39528,
                successor_registration_id=_SUCC_39528,
                successor_birth_id=_BIRTH_39528,
                min_receipt="dom_committed",
                retain_lane=True,
            ),
            ExecutionStore(),
        )
    assert result.send_verified is True
    assert result.receipt == "dom_committed"
    with reg._store.ports_lock():
        assert has_standdown_token(_PRED_39528) is True
    detached = reg.detach(_PRED_39528, reason="hop_detach")
    assert detached["registration_id"] == _PRED_39528
    assert _PRED_39528 not in reg._load_active()
