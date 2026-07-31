"""Tests for the atomic claim-and-post path in dispatch_cursor_sdk_generate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from systems.frontier_consult.cursor_sdk_generate import dispatch_cursor_sdk_generate
from systems.frontier_consult.handoff import PendingShellContention


@pytest.fixture()
def _base_generate_kwargs():
    return dict(
        request_id="req-test",
        role="cursor-sdk",
        model=None,
        subject=None,
        caller_agent="claude-web",
        contract="light-bounded",
        packet_path=None,
        message_text="test message",
        reuse_thread="999",
        bus_lifecycle=None,
        parent_dispatch_thread_id=None,
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
        read_only=False,
    )


class TestAutoConsolidationAtomicClaim:
    """claim_and_post_pointer_turn is called for is_auto_consolidation=True."""

    @pytest.mark.asyncio
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.claim_and_post_pointer_turn",
        new_callable=AsyncMock,
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        new_callable=AsyncMock,
        return_value=(True, {}),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.post_coord_admit_pointer",
        new_callable=AsyncMock,
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.resolve_cursor_sdk_generate_target",
        return_value=("cursor-sdk", "claude", "cursor", "composer"),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.get_profile",
        return_value=MagicMock(),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.build_sdk_generate_result",
        return_value={},
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.build_handoff_result",
        return_value={},
    )
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_generate_requested")
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_thread_created")
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome")
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        new_callable=AsyncMock,
    )
    async def test_auto_consolidation_calls_claim_not_post_pointer(
        self,
        mock_admit,
        _mock_outcome,
        _mock_thread_created,
        _mock_requested,
        _mock_build_result,
        _mock_build_handoff,
        _mock_get_profile,
        _mock_resolve_target,
        _mock_coord,
        _mock_worker,
        mock_claim,
        _base_generate_kwargs,
    ):
        """Successful atomic claim: claim_and_post called; admit skipped."""
        await dispatch_cursor_sdk_generate(
            **_base_generate_kwargs, is_auto_consolidation=True
        )
        mock_claim.assert_called_once()
        mock_admit.assert_not_called()

    @pytest.mark.asyncio
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.claim_and_post_pointer_turn",
        new_callable=AsyncMock,
        side_effect=PendingShellContention("Shell contention"),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        new_callable=AsyncMock,
        return_value="888",
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        new_callable=AsyncMock,
        return_value=(True, {}),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.post_coord_admit_pointer",
        new_callable=AsyncMock,
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.resolve_cursor_sdk_generate_target",
        return_value=("cursor-sdk", "claude", "cursor", "composer"),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.get_profile",
        return_value=MagicMock(),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.build_sdk_generate_result",
        return_value={},
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.build_handoff_result",
        return_value={},
    )
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_generate_requested")
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_thread_created")
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome")
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_pending_shell_contention_falls_to_create(
        self,
        mock_admit,
        _mock_outcome,
        _mock_thread_created,
        _mock_requested,
        _mock_build_result,
        _mock_build_handoff,
        _mock_get_profile,
        _mock_resolve_target,
        _mock_coord,
        _mock_worker,
        mock_create,
        mock_claim,
        _base_generate_kwargs,
    ):
        """On PendingShellContention: create_handoff_thread; admit once."""
        await dispatch_cursor_sdk_generate(
            **_base_generate_kwargs, is_auto_consolidation=True
        )
        mock_claim.assert_called_once()
        mock_create.assert_called_once()
        mock_admit.assert_called_once()


class TestExplicitReuseBypassesGate:
    """Explicit reuse_thread (is_auto_consolidation=False) uses post_pointer_turn."""

    @pytest.mark.asyncio
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.post_pointer_turn",
        new_callable=AsyncMock,
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.claim_and_post_pointer_turn",
        new_callable=AsyncMock,
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        new_callable=AsyncMock,
        return_value=(True, {}),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.post_coord_admit_pointer",
        new_callable=AsyncMock,
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.resolve_cursor_sdk_generate_target",
        return_value=("cursor-sdk", "claude", "cursor", "composer"),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.get_profile",
        return_value=MagicMock(),
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.build_sdk_generate_result",
        return_value={},
    )
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.build_handoff_result",
        return_value={},
    )
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_generate_requested")
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_thread_created")
    @patch("systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome")
    @patch(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_explicit_reuse_uses_post_pointer_not_claim(
        self,
        mock_admit,
        _mock_outcome,
        _mock_thread_created,
        _mock_requested,
        _mock_build_result,
        _mock_build_handoff,
        _mock_get_profile,
        _mock_resolve_target,
        _mock_coord,
        _mock_worker,
        mock_claim,
        mock_post_pointer,
        _base_generate_kwargs,
    ):
        """Explicit reuse bypasses the CAS gate."""
        await dispatch_cursor_sdk_generate(
            **_base_generate_kwargs, is_auto_consolidation=False
        )
        mock_post_pointer.assert_called_once()
        mock_claim.assert_not_called()
        mock_admit.assert_called_once()
