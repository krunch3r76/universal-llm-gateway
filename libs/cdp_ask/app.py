"""FastAPI satellite for Jupiter CDP project-ask (submit / poll / abort)."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from deploy_identity.code_version import resolve_code_version
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.models import (
    AbortExecutionResponse,
    ExecutionPollResponse,
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    SubmitProjectAskRequest,
    SubmitProjectAskResponse,
    classify_stall_stage,
)
from cdp_ask.page_liveness import LadderCallbacks
from cdp_ask.registry_hygiene_loop import RegistryHygieneLoop
from cdp_ask.runner import (
    run_execution,
    verify_harvest_root,
)

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Liveness plus the running code version and process identity.

    ``code_version`` exists so the fleet propagation rule
    (``git merge-base --is-ancestor <sha> <running_sha>``) is applicable to
    this satellite. Same field name and same resolution mechanism as
    ``mcp-server`` /health and ``git_integration_worker`` liveness; ``unknown``
    when the process cannot attribute a SHA to its own loaded code.

    ``pid`` is ``os.getpid()`` of the uvicorn process serving ``/health`` —
    same field GIW liveness / MCP ``health_json`` use for
    ``strong_process_identity``.
    """

    status: str
    harvest_root: str
    harvest_root_ok: bool
    registry_hygiene: str
    code_version: str
    pid: int


def create_app(*, store: ExecutionStore | None = None) -> FastAPI:
    app = FastAPI(
        title="CDP Project Ask",
        description="Jupiter satellite for claude.ai CDP sealed asks",
    )
    execution_store = store or ExecutionStore()
    registry_hygiene = RegistryHygieneLoop()

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
        await registry_hygiene.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await registry_hygiene.stop()
        await execution_store.stop()

    @app.get("/v1/project-ask/active-work")
    async def active_work() -> dict[str, object]:
        """In-flight executions for restart drain + multi-lane admission.

        ``busy`` ⇒ defer restart (any pending/running). Lane admission uses
        ``free_slots`` / ``at_hard_limit`` (soft=2, hard=3) — ¬ equate busy
        with lane-full (friction a:25814). ``rows`` lists per-flight
        ``registration_id`` / ``holder`` / ``purpose`` for warm followup discovery.
        """
        return await execution_store.active_work_snapshot()

    @app.post(
        "/v1/project-ask/followups",
        response_model=FollowupProjectAskResponse,
    )
    async def followup(req: FollowupProjectAskRequest) -> FollowupProjectAskResponse:
        """Warm paste into a live retained Cowork CSE (attached lane only).

        Synchronous paste-proof — no ``execution_store.create``, no reply harvest.
        """
        verify_harvest_root()
        return await execute_followup(req, execution_store)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        try:
            root = verify_harvest_root()
        except RuntimeError:
            return HealthResponse(
                status="fail_closed",
                harvest_root="",
                harvest_root_ok=False,
                registry_hygiene="stopped",
                code_version=resolve_code_version(),
                pid=os.getpid(),
            )
        hygiene_status = "running" if registry_hygiene.running else "stopped"
        return HealthResponse(
            status="ok",
            harvest_root=str(root),
            harvest_root_ok=True,
            registry_hygiene=hygiene_status,
            code_version=resolve_code_version(),
            pid=os.getpid(),
        )

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

        async def _on_turn_idle() -> None:
            # Liveness is deliberately retained past turn_idle: it is the only
            # per-sample progress signal a poller can use to tell post-idle
            # harvest work apart from a hang (friction a:26175).
            await execution_store.update_ladder(
                record.execution_id,
                completion_phase="turn_idle",
                turn_idle_at=time.time(),
            )

        async def _on_content_proof(uri: str, sha: str) -> None:
            await execution_store.update_ladder(
                record.execution_id,
                completion_phase="content_proof",
                content_proof_uri=uri,
                content_proof_sha256=sha,
            )

        async def _on_archiving() -> None:
            await execution_store.update_ladder(
                record.execution_id,
                completion_phase="archiving",
            )

        async def _on_liveness(
            streaming: bool,
            stop: bool,
            tool_pause: bool,
            observed_at: float,
        ) -> None:
            await execution_store.update_liveness(
                record.execution_id,
                streaming=streaming,
                stop=stop,
                tool_pause=tool_pause,
                liveness_observed_at=observed_at,
            )

        ladder = LadderCallbacks(
            on_turn_idle=_on_turn_idle,
            on_content_proof=_on_content_proof,
            on_archiving=_on_archiving,
            on_liveness=_on_liveness,
            abort_check=_abort_check,
        )

        async def _runner() -> None:
            try:
                payload = await run_execution(
                    req,
                    execution_id=record.execution_id,
                    abort_check=_abort_check,
                    on_registered=_sync_registered,
                    ladder=ladder,
                )
                if payload.get("awaiting_wake_debt") and payload.get("ok"):
                    await execution_store.mark_awaiting_wake(
                        record.execution_id,
                        result=payload,
                    )
                    return
                status = (
                    "aborted"
                    if payload.get("status") == "aborted"
                    else ("completed" if payload.get("ok") else "failed")
                )
                stall = payload.get("stall_stage")
                if status == "failed" and not stall:
                    stall = classify_stall_stage(payload.get("error"))
                await execution_store.mark_terminal(
                    record.execution_id,
                    status=status,
                    result=payload,
                    error=payload.get("error"),
                    stall_stage=stall if status == "failed" else None,
                )
            except asyncio.CancelledError:
                await execution_store.mark_terminal(
                    record.execution_id,
                    status="aborted",
                    error="cancelled",
                    stall_stage="mark_terminal",
                )
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("execution %s failed", record.execution_id)
                await execution_store.mark_terminal(
                    record.execution_id,
                    status="failed",
                    error=str(exc),
                    stall_stage="mark_terminal",
                )

        task = asyncio.create_task(_runner())
        await execution_store.attach_task(record.execution_id, task)

        return SubmitProjectAskResponse(
            execution_id=record.execution_id,
            status="running",
            registration_id=None,
            terminal=False,
            phase="admitted",
            handoff_status="awaiting_first_reply",
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
        live = record.status == "running"
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
            harvest_provenance=payload.get("harvest_provenance"),
            completion_phase=record.completion_phase,
            content_proof_uri=record.content_proof_uri,
            content_proof_sha256=record.content_proof_sha256,
            turn_idle_at=record.turn_idle_at,
            stall_stage=record.stall_stage,
            streaming=record.streaming if live else None,
            stop=record.stop if live else None,
            tool_pause=record.tool_pause if live else None,
            liveness_observed_at=record.liveness_observed_at if live else None,
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
    app.state.registry_hygiene = registry_hygiene
    return app
