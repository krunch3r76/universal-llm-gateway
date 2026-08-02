"""Tests for the atomic claim-and-post path in dispatch_cursor_sdk_generate."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate as generate_mod
from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.handoff import PendingShellContention


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


class TestAutoConsolidationAtomicClaim:
    """claim_and_post_pointer_turn is called for is_auto_consolidation=True."""

    @pytest.mark.asyncio
    async def test_auto_consolidation_calls_claim_not_post_pointer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful atomic claim: claim_and_post called; admit skipped."""
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

        await generate_mod.dispatch_cursor_sdk_generate(
            request_id="req-test",
            role="cursor-sdk",
            model=None,
            subject=None,
            caller_agent="claude-web",
            contract="light-bounded",
            packet_path=None,
            message_text="test message",
            reuse_thread="999",
            is_auto_consolidation=True,
        )

        claim.assert_awaited_once()
        post_pointer.assert_not_awaited()
        admit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_shell_contention_falls_to_create(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On PendingShellContention: create_handoff_thread; admit once."""
        _patch_generate_deps(monkeypatch)
        claim = AsyncMock(side_effect=PendingShellContention("Shell contention"))
        create_thread = AsyncMock(return_value="888")
        admit = AsyncMock(return_value=True)
        worker = AsyncMock(return_value=(True, {"dispatch_id": "d1"}))

        monkeypatch.setattr(prepare_mod, "claim_and_post_pointer_turn", claim)
        monkeypatch.setattr(prepare_mod, "create_handoff_thread", create_thread)
        monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)
        monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
        monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker_message", worker)

        await generate_mod.dispatch_cursor_sdk_generate(
            request_id="req-test",
            role="cursor-sdk",
            model=None,
            subject=None,
            caller_agent="claude-web",
            contract="light-bounded",
            packet_path=None,
            message_text="test message",
            reuse_thread="999",
            is_auto_consolidation=True,
        )

        claim.assert_awaited_once()
        create_thread.assert_awaited_once()
        admit.assert_awaited_once()


class TestExplicitReuseBypassesGate:
    """Explicit reuse_thread (is_auto_consolidation=False) uses post_pointer_turn."""

    @pytest.mark.asyncio
    async def test_explicit_reuse_uses_post_pointer_not_claim(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit reuse bypasses the CAS gate."""
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

        await generate_mod.dispatch_cursor_sdk_generate(
            request_id="req-test",
            role="cursor-sdk",
            model=None,
            subject=None,
            caller_agent="claude-web",
            contract="light-bounded",
            packet_path=None,
            message_text="test message",
            reuse_thread="999",
            is_auto_consolidation=False,
        )

        post_pointer.assert_awaited_once()
        claim.assert_not_awaited()
        admit.assert_awaited_once()
