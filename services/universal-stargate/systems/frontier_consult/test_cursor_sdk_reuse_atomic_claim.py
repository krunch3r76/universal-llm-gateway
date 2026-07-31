"""Tests for F3 atomic claim-and-post on cursor-sdk generate reuse path."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate as generate_mod
from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.handoff import (
    PendingShellContention,
    claim_and_post_pointer_turn,
)


def _patch_generate_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_mod,
        "resolve_cursor_sdk_generate_target",
        lambda *_a, **_k: (
            "cursor-sdk:dispatch:exec-fixture",
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
        lambda **_k: (False, False, ""),
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
    monkeypatch.setattr(prepare_mod, "emit_sdk_generate_requested", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)
    monkeypatch.setattr(generate_mod, "emit_sdk_worker_outcome", lambda **_k: None)


@pytest.mark.asyncio
async def test_claim_and_post_pointer_turn_raises_on_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        status_code = 409

        @staticmethod
        def json() -> dict[str, str]:
            return {
                "detail": {
                    "error": "pending_shell_contention",
                    "message": "shell taken",
                }
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        lambda *_a, **_k: _Client(),
    )
    with pytest.raises(PendingShellContention):
        await claim_and_post_pointer_turn(
            request_id="req-1",
            thread_id="9001",
            to_agent="cursor-sdk:dispatch:exec-1",
            subject="implement",
            pointer_body="packet",
            caller_agent="dispatch",
            execution_id="exec-1",
            pipeline_id="cursor-sdk-generate",
        )


@pytest.mark.asyncio
async def test_auto_consolidation_uses_atomic_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generate_deps(monkeypatch)
    claim = AsyncMock()
    post_pointer = AsyncMock()
    create_thread = AsyncMock(return_value="2701")
    admit = AsyncMock(return_value=True)
    worker = AsyncMock(return_value=(True, {"dispatch_id": "d1"}))

    monkeypatch.setattr(prepare_mod, "claim_and_post_pointer_turn", claim)
    monkeypatch.setattr(prepare_mod, "post_pointer_turn", post_pointer)
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", create_thread)
    monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker_message", worker)

    result = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-auto",
        role="cursor-sdk",
        model=None,
        subject="implement",
        caller_agent="dispatch",
        contract="pure-mechanical",
        packet_path=None,
        message_text="do work",
        reuse_thread="9001",
        is_auto_consolidation=True,
    )

    assert result["thread_id"] == "9001"
    claim.assert_awaited_once()
    post_pointer.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_reuse_bypasses_atomic_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generate_deps(monkeypatch)
    claim = AsyncMock()
    post_pointer = AsyncMock()
    admit = AsyncMock(return_value=True)
    worker = AsyncMock(return_value=(True, {"dispatch_id": "d1"}))

    monkeypatch.setattr(prepare_mod, "claim_and_post_pointer_turn", claim)
    monkeypatch.setattr(prepare_mod, "post_pointer_turn", post_pointer)
    monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker_message", worker)

    result = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-explicit",
        role="cursor-sdk",
        model=None,
        subject="implement",
        caller_agent="dispatch",
        contract="pure-mechanical",
        packet_path=None,
        message_text="do work",
        reuse_thread="2700",
        is_auto_consolidation=False,
    )

    assert result["thread_id"] == "2700"
    claim.assert_not_awaited()
    post_pointer.assert_awaited_once()
    admit.assert_awaited_once()


@pytest.mark.asyncio
async def test_contention_falls_back_to_create_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generate_deps(monkeypatch)
    claim = AsyncMock(side_effect=PendingShellContention("taken"))
    create_thread = AsyncMock(return_value="2702")
    admit = AsyncMock(return_value=True)
    worker = AsyncMock(return_value=(True, {"dispatch_id": "d1"}))

    monkeypatch.setattr(prepare_mod, "claim_and_post_pointer_turn", claim)
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", create_thread)
    monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker_message", worker)

    result = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-fallback",
        role="cursor-sdk",
        model=None,
        subject="implement",
        caller_agent="dispatch",
        contract="pure-mechanical",
        packet_path=None,
        message_text="do work",
        reuse_thread="9001",
        is_auto_consolidation=True,
    )

    create_thread.assert_awaited_once()
    admit.assert_awaited_once()
    assert result["thread_id"] == "2702"
