"""D-6 orchestration truthfulness for cursor-sdk worker dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate as generate_mod
from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.admission import FrontierEndpointError


def _patch_prepare_for_worker_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_mod,
        "resolve_cursor_sdk_generate_target",
        lambda *_a, **_k: (
            "cursor-sdk:dispatch:exec",
            "cursor",
            "sdk",
            "composer-2.5",
        ),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.densify_triage.validate_generate_density_intake",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.prepare_lb_auto_review_for_generate",
        lambda **_k: (False, False, "packet"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.validate_generate_contract_packet_rules",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        prepare_mod,
        "align_cursor_knobs",
        lambda **_k: type(
            "A",
            (),
            {
                "aligned_knobs": None,
                "warnings_as_dicts": lambda self: [],
                "knob_resolution_as_dicts": lambda self: [],
            },
        )(),
    )
    monkeypatch.setattr(
        prepare_mod, "create_handoff_thread", AsyncMock(return_value="thread-1")
    )
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(prepare_mod, "emit_sdk_generate_requested", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)


@pytest.mark.asyncio
async def test_worker_409_raises_no_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_prepare_for_worker_tests(monkeypatch)

    async def _worker(*_args: object, **_kwargs: object) -> tuple[bool, dict]:
        return False, {
            "status_code": 409,
            "code": "CURSOR_DISPATCH_CONFLICT",
            "message": "idempotency conflict",
            "blocking_dispatch_id": "block-1",
        }

    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", _worker)
    monkeypatch.setattr(generate_mod, "emit_sdk_worker_outcome", lambda **_k: None)

    with pytest.raises(FrontierEndpointError) as excinfo:
        await generate_mod.dispatch_cursor_sdk_generate(
            request_id="req-409",
            role="cursor-sdk",
            model=None,
            subject="s",
            caller_agent="dispatch",
            contract="implement",
            packet_path="tmp/packet.md",
            message_text=None,
        )
    err = excinfo.value
    assert err.details is not None
    assert err.details.get("blocking_dispatch_id") == "block-1"
    assert err.details.get("status_code") == 409


@pytest.mark.asyncio
async def test_worker_dispatched_stamps_both_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_for_worker_tests(monkeypatch)
    dispatched: list[object] = []

    async def _worker(*_args: object, **_kwargs: object) -> tuple[bool, dict]:
        return True, {
            "status_code": 200,
            "dispatch_id": "disp-x",
            "ticket": {},
        }

    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", _worker)
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        lambda event: (
            dispatched.append(event)
            if event.signal == "frontier.sdk.worker.dispatched"
            else None
        ),
    )

    await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-dual-stamp",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="implement",
        packet_path="tmp/packet.md",
        message_text=None,
    )
    assert len(dispatched) == 1
    event = dispatched[0]
    assert event.signal == "frontier.sdk.worker.dispatched"
    assert event.payload["execution_id"]
    assert event.payload["dispatch_id"] == "disp-x"


@pytest.mark.asyncio
async def test_worker_202_returns_queued_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_for_worker_tests(monkeypatch)
    dispatched: list[object] = []
    queued: list[object] = []

    async def _worker(*_args: object, **_kwargs: object) -> tuple[bool, dict]:
        return True, {
            "status_code": 202,
            "queued": True,
            "ticket": {
                "dispatch_id": "disp-q",
                "thread_id": "thread-1",
                "status": "queued",
                "queue_position": 1,
            },
        }

    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", _worker)
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        lambda event: (
            dispatched.append(event)
            if event.signal == "frontier.sdk.worker.dispatched"
            else queued.append(event)
        ),
    )

    result = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-202",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="implement",
        packet_path="tmp/packet.md",
        message_text=None,
    )
    assert result["status"] == "queued"
    assert result["queue_ticket"]["dispatch_id"] == "disp-q"
    assert dispatched == []
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_worker_timeout_hard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_prepare_for_worker_tests(monkeypatch)

    async def _worker(*_args: object, **_kwargs: object) -> tuple[bool, dict]:
        return False, {
            "status_code": 599,
            "code": "CURSOR_WORKER_UNREACHABLE",
            "message": "timeout",
        }

    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", _worker)
    monkeypatch.setattr(generate_mod, "emit_sdk_worker_outcome", lambda **_k: None)

    with pytest.raises(FrontierEndpointError):
        await generate_mod.dispatch_cursor_sdk_generate(
            request_id="req-timeout",
            role="cursor-sdk",
            model=None,
            subject="s",
            caller_agent="dispatch",
            contract="implement",
            packet_path="tmp/packet.md",
            message_text=None,
        )
