"""Paste authorization, identity refusal, and idempotent replay tests."""

from __future__ import annotations

import json
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


@pytest.mark.asyncio
async def test_paste_identity_omission_refused() -> None:
    result = await execute_paste(PasteRequest(prompt_text="hi"), ExecutionStore())
    assert result.code == "identity_required"


@pytest.mark.asyncio
async def test_unauthorized_cross_lane_403_shape() -> None:
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={
                "state": "current",
                "registration_id": "target-reg",
                "parent_thread_claim": "lane-1",
            },
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(registration_id="target-reg", prompt_text="hi"),
            ExecutionStore(),
        )
    assert result.code == "paste_unauthorized"
    assert followup.await_count == 0


@pytest.mark.asyncio
async def test_self_supersession_refuses_paste() -> None:
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={"state": "current", "registration_id": "same-reg"},
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="same-reg",
                caller_registration_id="same-reg",
                prompt_text="hi",
            ),
            ExecutionStore(),
        )
    assert result.code == "self_supersession"
    assert followup.await_count == 0


@pytest.mark.asyncio
async def test_idempotent_replay() -> None:
    store = ExecutionStore()
    req = PasteRequest(
        registration_id="target-reg",
        prompt_text="hi",
        grant="explicit",
        idempotency_key="key-1",
    )
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="target-reg",
    )
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={"state": "current", "registration_id": "target-reg"},
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
    ):
        first = await execute_paste(req, store)
        second = await execute_paste(req, store)
    assert first.replayed is False
    assert second.replayed is True
    assert followup.await_count == 1


@pytest.mark.asyncio
async def test_hop_pair_request_triple_authorizes_paste() -> None:
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="target-reg",
    )

    def _prov(**kwargs: object) -> dict[str, object]:
        reg = str(kwargs.get("registration_id") or "")
        if reg == "caller-reg":
            return {
                "state": "current",
                "registration_id": "caller-reg",
                "parent_thread_proven": "7437",
            }
        return {
            "state": "current",
            "registration_id": "target-reg",
            "parent_thread_proven": "7437",
        }

    with (
        patch("cdp_ask.cse_session_paste.resolve_provenance", side_effect=_prov),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="target-reg",
                caller_registration_id="caller-reg",
                superseded_registration_id="target-reg",
                parent_thread="7437",
                prompt_text="stand down",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    assert followup.await_count == 1


@pytest.mark.asyncio
async def test_stand_down_authorized_from_hop_watch_without_request_triple(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successor stand_down needs only the target — hop already recorded the pair."""
    watch = tmp_path / "hop_cadence_watches.json"
    watch.write_text(
        json.dumps(
            {
                "7437": {
                    "thread_id": "7437",
                    "superseded_registration_id": "pred-reg",
                    "successor_birth_id": "birth-1",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch))
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="pred-reg",
    )
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={
                "state": "current",
                "registration_id": "pred-reg",
                "parent_thread_proven": "7437",
            },
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="pred-reg",
                envelope="stand_down",
                prompt_text="TYPE: SEAT_STAND_DOWN",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    assert followup.await_count == 1


@pytest.mark.asyncio
async def test_free_envelope_does_not_use_hop_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = tmp_path / "hop_cadence_watches.json"
    watch.write_text(
        json.dumps({"7437": {"superseded_registration_id": "pred-reg"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch))
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={"state": "current", "registration_id": "pred-reg"},
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(registration_id="pred-reg", prompt_text="hi"),
            ExecutionStore(),
        )
    assert result.code == "paste_unauthorized"
    assert followup.await_count == 0


@pytest.mark.asyncio
async def test_stand_down_same_lane_claimant_without_watch_pair() -> None:
    """Colliding CSEs on one parent_thread may not be the hop-watch predecessor."""
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="live-pred",
    )

    def _prov(**kwargs: object) -> dict[str, object]:
        reg = str(kwargs.get("registration_id") or "")
        if reg == "live-succ":
            return {
                "state": "current",
                "registration_id": "live-succ",
                "parent_thread_proven": "7437",
            }
        return {
            "state": "current",
            "registration_id": "live-pred",
            "parent_thread_proven": "7437",
        }

    with (
        patch("cdp_ask.cse_session_paste.resolve_provenance", side_effect=_prov),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7437": {"superseded_registration_id": "other-hop-pred"}},
        ),
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="live-pred",
                caller_registration_id="live-succ",
                envelope="stand_down",
                prompt_text="TYPE: SEAT_STAND_DOWN",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    assert followup.await_count == 1


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
