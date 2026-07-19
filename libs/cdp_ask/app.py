"""FastAPI satellite for Jupiter CDP project-ask (submit / poll / abort)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import (
    AbortExecutionResponse,
    ExecutionPollResponse,
    SubmitProjectAskRequest,
    SubmitProjectAskResponse,
)
from cdp_ask.runner import run_execution, verify_harvest_root

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    harvest_root: str
    harvest_root_ok: bool


def create_app(*, store: ExecutionStore | None = None) -> FastAPI:
    app = FastAPI(
        title="CDP Project Ask",
        description="Jupiter satellite for claude.ai CDP sealed asks",
    )
    execution_store = store or ExecutionStore()

    def _deregister(registration_id: str) -> None:
        from claude_bundles import cdp_registry

        cdp_registry.deregister_lane(registration_id, kill=True)

    execution_store.bind_deregister(_deregister)

    @app.on_event("startup")
    async def _startup() -> None:
        verify_harvest_root()
        reaped = await execution_store.boot_reconcile()
        if reaped:
            logger.warning("boot reconcile reaped orphaned lanes: %s", reaped)
        await execution_store.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await execution_store.stop()

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        try:
            root = verify_harvest_root()
        except RuntimeError:
            return HealthResponse(
                status="fail_closed", harvest_root="", harvest_root_ok=False
            )
        return HealthResponse(status="ok", harvest_root=str(root), harvest_root_ok=True)

    @app.post(
        "/v1/project-ask/executions",
        response_model=SubmitProjectAskResponse,
        status_code=202,
    )
    async def submit_execution(
        req: SubmitProjectAskRequest,
    ) -> SubmitProjectAskResponse:
        verify_harvest_root()
        record = await execution_store.create(holder=req.holder, purpose=req.purpose)

        async def _abort_check() -> bool:
            current = await execution_store.get(record.execution_id)
            return bool(current and current.abort_requested)

        async def _on_registered(registration_id: str) -> None:
            await execution_store.set_registration_id(
                record.execution_id, registration_id
            )

        def _sync_registered(registration_id: str) -> None:
            asyncio.create_task(_on_registered(registration_id))

        async def _runner() -> None:
            try:
                payload = await run_execution(
                    req,
                    abort_check=_abort_check,
                    on_registered=_sync_registered,
                )
                status = (
                    "aborted"
                    if payload.get("status") == "aborted"
                    else ("completed" if payload.get("ok") else "failed")
                )
                await execution_store.mark_terminal(
                    record.execution_id,
                    status=status,
                    result=payload,
                    error=payload.get("error"),
                )
            except asyncio.CancelledError:
                await execution_store.mark_terminal(
                    record.execution_id,
                    status="aborted",
                    error="cancelled",
                )
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("execution %s failed", record.execution_id)
                await execution_store.mark_terminal(
                    record.execution_id,
                    status="failed",
                    error=str(exc),
                )

        task = asyncio.create_task(_runner())
        await execution_store.attach_task(record.execution_id, task)

        return SubmitProjectAskResponse(
            execution_id=record.execution_id,
            status="running",
            registration_id=None,
        )

    @app.get(
        "/v1/project-ask/executions/{execution_id}",
        response_model=ExecutionPollResponse,
    )
    async def poll_execution(execution_id: str) -> ExecutionPollResponse:
        record = await execution_store.get(execution_id)
        if record is None:
            raise HTTPException(404, f"unknown execution_id: {execution_id}")
        payload = record.result or {}
        return ExecutionPollResponse(
            execution_id=record.execution_id,
            status=record.status,
            registration_id=record.registration_id,
            ok=payload.get("ok"),
            archive_uri=payload.get("archive_uri"),
            body=payload.get("body"),
            body_len=payload.get("body_len"),
            url=payload.get("url"),
            project_uuid=payload.get("project_uuid"),
            project_url=payload.get("project_url"),
            model=payload.get("model"),
            attested_model=payload.get("attested_model"),
            error=record.error or payload.get("error"),
            delete_after=payload.get("delete_after"),
            results=payload.get("results"),
        )

    @app.post(
        "/v1/project-ask/executions/{execution_id}/abort",
        response_model=AbortExecutionResponse,
    )
    async def abort_execution(execution_id: str) -> AbortExecutionResponse:
        from claude_bundles.project_ask_abort import (
            AbortCleanupOutcome,
            abort_cleanup,
            lookup_active_registration,
        )

        record = await execution_store.request_abort(execution_id)
        if record is None:
            raise HTTPException(404, f"unknown execution_id: {execution_id}")

        outcome: AbortCleanupOutcome
        if record.registration_id:
            reg = lookup_active_registration(record.registration_id)
            if reg is not None:
                outcome = abort_cleanup(reg, purpose=record.purpose)
            else:
                outcome = "no_registration"
        else:
            outcome = "no_registration"

        terminal = outcome == "attested_stopped_and_deregistered"
        attested = outcome in {
            "attested_stopped_and_deregistered",
            "stopped_deregister_failed",
        }
        still_attached: bool | None
        if outcome == "still_attached":
            still_attached = True
        elif attested:
            still_attached = False
        else:
            still_attached = None

        # M3: cleanup before task.cancel so still_attached keeps a live driver for
        # retry; non-terminal failed-attest rows stay running + abort_requested until
        # the caller retries abort or execution_store TTL reaper marks them failed.
        if terminal and record.task and not record.task.done():
            record.task.cancel()
        if terminal:
            await execution_store.mark_terminal(
                execution_id,
                status="aborted",
                error="aborted",
            )
            return AbortExecutionResponse(
                execution_id=execution_id,
                status="aborted",
                aborted=True,
                attested=True,
                still_attached=False,
                abort_outcome=outcome,
            )

        refreshed = await execution_store.get(execution_id)
        status = refreshed.status if refreshed else record.status
        return AbortExecutionResponse(
            execution_id=execution_id,
            status=status,
            aborted=False,
            attested=attested,
            still_attached=still_attached,
            abort_outcome=outcome,
        )

    app.state.execution_store = execution_store
    return app
