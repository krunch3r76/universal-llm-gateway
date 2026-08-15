"""Unit tests for post-drain git-worker activation verification and settle wiring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from charter_runner_store.propagation_validation import (
    mint_pending_validation_for_intent,
)

from scripts.model_manager.ui.controller.git_worker_activation_verify import (
    ACTIVATION_IDLE_TIMEOUT_S,
    arms_activation_verify,
    run_activation_verify,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_VERIFYING_ACTIVATION,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore

_RESOLVABLE_CODE_REF = "ab53680e92543c316b16aef9a1412cd652c2a56b"


def _run(coro):
    return asyncio.run(coro)


def test_arms_activation_verify_restart_only() -> None:
    """Only restart-shaped manage actions require post-kill activation verification."""
    assert arms_activation_verify("restart")
    assert arms_activation_verify("sync_restart")
    assert not arms_activation_verify("stop")


def test_missing_kill_boundary_times_out(tmp_path, monkeypatch) -> None:
    """Verify without a kill boundary must terminalize as activation_unverified quickly."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_VERIFYING_ACTIVATION,
    )

    validation_id = mint_pending_validation_for_intent(
        intent, code_ref=_RESOLVABLE_CODE_REF
    )
    _run(
        run_activation_verify(
            store,
            intent.intent_id,
            validation_id,
            idle_timeout_s=0.01,
        )
    )
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == "activation_unverified"


def test_expired_kill_boundary_budget_terminalizes_without_reset(tmp_path, monkeypatch) -> None:
    """An already-expired settle budget must fail closed on entry, not extend the clock."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_VERIFYING_ACTIVATION,
    )
    expired_boundary = (datetime.now(UTC) - timedelta(seconds=ACTIVATION_IDLE_TIMEOUT_S + 30)).isoformat()
    store.set_kill_boundary(intent.intent_id, kill_boundary_at=expired_boundary)

    unreachable_probe = {"probe_reachable": False}
    with patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        return_value=unreachable_probe,
    ):
        validation_id = mint_pending_validation_for_intent(
            intent, code_ref=_RESOLVABLE_CODE_REF
        )
        _run(
            run_activation_verify(
                store,
                intent.intent_id,
                validation_id,
                idle_timeout_s=ACTIVATION_IDLE_TIMEOUT_S,
            )
        )
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == "activation_unverified"
    assert got.reason == "idle_timeout"


def test_activation_verify_invokes_settle_with_validation_ids(
    tmp_path, monkeypatch
) -> None:
    """Ready-join settle during verify must thread intent and validation identifiers."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_VERIFYING_ACTIVATION,
    )
    kill_boundary = datetime.now(UTC).isoformat()
    store.set_kill_boundary(intent.intent_id, kill_boundary_at=kill_boundary)
    validation_id = mint_pending_validation_for_intent(
        intent, code_ref=_RESOLVABLE_CODE_REF
    )
    settle_calls: list[dict[str, str | float | None]] = []

    async def _capture_settle(
        service: str,
        *,
        settle_not_before_monotonic: float,
        source: str,
        restart_intent: str | None = None,
        validation_id: str | None = None,
        window_deadline_at: str | None = None,
    ) -> None:
        settle_calls.append(
            {
                "service": service,
                "source": source,
                "restart_intent": restart_intent,
                "validation_id": validation_id,
                "settle_not_before_monotonic": settle_not_before_monotonic,
            }
        )

    with patch(
        "scripts.model_manager.ui.controller.propagation_settle_hook.invoke_propagation_settle_for_service",
        _capture_settle,
    ), patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        return_value={"probe_reachable": False},
    ):
        _run(
            run_activation_verify(
                store,
                intent.intent_id,
                validation_id,
                idle_timeout_s=0.01,
            )
        )
    assert len(settle_calls) == 1
    assert settle_calls[0]["service"] == "git_integration_worker"
    assert settle_calls[0]["source"] == "drain"
    assert settle_calls[0]["restart_intent"] == intent.intent_id
    assert settle_calls[0]["validation_id"] == validation_id


def test_activation_verify_settle_closes_open_row(tmp_path, monkeypatch) -> None:
    """Ready-join/settle from verify closes an open row and threads validation ids."""
    from charter_runner_store.db import open_ledger_db
    from charter_runner_store.propagation_ledger import list_open_rows, upsert_open_rows
    from implement_admission.propagation_row import PropagationRow

    from scripts.model_manager.ui.controller.git_worker_activation_verify import (
        _invoke_activation_settle,
    )

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "ab53680e92543c316b16aef9a1412cd652c2a56b"
    upsert_open_rows(
        [
            PropagationRow(
                service="mcp",
                code_ref=sha,
                action="sync_restart",
                safe_window="drain_required",
                proof_class="process_live",
            )
        ]
    )
    row_id = list_open_rows()[0].row_id
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="mcp",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_VERIFYING_ACTIVATION,
    )
    store.set_kill_boundary(intent.intent_id, kill_boundary_at=datetime.now(UTC).isoformat())
    validation_id = mint_pending_validation_for_intent(intent, code_ref=sha)
    from charter_runner_store.propagation_terminal import settle_open_row

    def _fake_settle(service: str, _probe: object, **kwargs: object) -> list[object]:
        return [
            settle_open_row(
                row,
                lambda _s: {"code_version": sha},
                settle_not_before_monotonic=kwargs.get("settle_not_before_monotonic"),
            )
            for row in list_open_rows()
            if row.service == service
        ]

    monkeypatch.setattr(
        "charter_runner_store.propagation_terminal.settle_open_rows_for_service",
        _fake_settle,
    )
    _run(_invoke_activation_settle(store, intent.intent_id, validation_id))
    db = open_ledger_db()
    try:
        status = db.execute(
            "SELECT status FROM propagation_ledger WHERE row_id=?", (row_id,)
        ).fetchone()
        pending = db.execute(
            "SELECT validation_id FROM propagation_validation WHERE validation_id=?",
            (validation_id,),
        ).fetchone()
    finally:
        db.close()
    assert status is not None
    assert status["status"] == "closed"
    assert pending is not None
