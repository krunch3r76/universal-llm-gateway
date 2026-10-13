"""Regression: tick-scan reconcile mints without window terminal."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner import (
    tick_friction_reconcile as mod,
)


@pytest.mark.asyncio
async def test_reconcile_enrolled_roots_calls_reconcile_only() -> None:
    roots = [{"id": "5975", "tags": ["charter-runner"]}]

    with patch(
        "cortex_store.dispatch_ops._friction_enqueue.reconcile_charter_frictions",
        return_value=[{"todo_id": "todo:friction-26595", "assertion_id": 26595}],
    ) as reconcile:
        minted = await mod.reconcile_enrolled_roots_on_tick(roots)

    assert minted == [{"todo_id": "todo:friction-26595", "assertion_id": 26595}]
    reconcile.assert_called_once_with("5975")


@pytest.mark.asyncio
async def test_reconcile_skips_blank_root_ids() -> None:
    with patch(
        "cortex_store.dispatch_ops._friction_enqueue.reconcile_charter_frictions"
    ) as reconcile:
        minted = await mod.reconcile_enrolled_roots_on_tick([{"id": ""}, {}])
    assert minted == []
    reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_failure_does_not_abort_remaining_roots() -> None:
    roots = [
        {"id": "5975", "tags": ["charter-runner"]},
        {"id": "6091", "tags": ["charter-runner"]},
    ]

    def _reconcile(root_id: str) -> list[dict]:
        if root_id == "5975":
            raise RuntimeError("boom")
        return [{"todo_id": "todo:friction-26603", "assertion_id": 26603}]

    with (
        patch(
            "cortex_store.dispatch_ops._friction_enqueue.reconcile_charter_frictions",
            side_effect=_reconcile,
        ),
        patch.object(mod.logger, "exception", new=MagicMock()),
    ):
        minted = await mod.reconcile_enrolled_roots_on_tick(roots)

    assert minted == [{"todo_id": "todo:friction-26603", "assertion_id": 26603}]
