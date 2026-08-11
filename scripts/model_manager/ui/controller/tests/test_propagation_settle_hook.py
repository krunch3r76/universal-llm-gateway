"""Tests for shared propagation settle hook looked-empty observability."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from scripts.model_manager.ui.controller.propagation_settle_hook import (
    invoke_propagation_settle_for_service,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_looked_empty_emits_when_no_open_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    events: list[tuple[str, dict[str, Any]]] = []

    async def _capture_emit(
        service: str, *, settle_not_before_monotonic: float, source: str
    ) -> None:
        events.append(
            (
                service,
                {
                    "settle_not_before_monotonic": settle_not_before_monotonic,
                    "source": source,
                },
            )
        )

    with patch(
        "scripts.model_manager.ui.controller.propagation_settle_hook.emit_manage_propagation_settle_looked_empty",
        _capture_emit,
    ):
        _run(
            invoke_propagation_settle_for_service(
                "mcp",
                settle_not_before_monotonic=123.45,
                source="lifecycle_wrapper",
            )
        )
    assert len(events) == 1
    assert events[0][0] == "mcp"
    assert events[0][1]["source"] == "lifecycle_wrapper"


def test_looked_empty_not_emitted_when_rows_settled(tmp_path, monkeypatch) -> None:
    from charter_runner_store.db import open_ledger_db

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    db = open_ledger_db()
    try:
        db.execute(
            """
            INSERT INTO propagation_ledger (
              row_id, service, action, code_ref, safe_window, proof, proof_class,
              status, age_in_harvests, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 1.0)
            """,
            (
                f"mcp:{sha}:sync_restart",
                "mcp",
                "sync_restart",
                sha,
                "drain_required",
                "probe",
                "process_live",
            ),
        )
        db.commit()
    finally:
        db.close()
    emitted = False

    async def _capture_emit(*_args: Any, **_kwargs: Any) -> None:
        nonlocal emitted
        emitted = True

    with patch(
        "charter_runner_store.propagation_terminal_batch.default_probe",
        lambda _service: {"code_version": sha},
    ), patch(
        "scripts.model_manager.ui.controller.propagation_settle_hook.emit_manage_propagation_settle_looked_empty",
        _capture_emit,
    ):
        _run(
            invoke_propagation_settle_for_service(
                "mcp",
                settle_not_before_monotonic=999.0,
                source="drain",
            )
        )
    assert emitted is False
