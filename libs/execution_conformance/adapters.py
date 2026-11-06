"""Read-only wrappers over existing actuator surfaces (no MCP runtime import)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from .states import ExecutionState


@dataclass(frozen=True, slots=True)
class OwnerLookup:
    found: bool
    actuator_ref: str | None = None
    state: ExecutionState | None = None
    durable: bool = False
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AdmitOutcome:
    actuator_ref: str
    state: ExecutionState


class ActuatorAdapter(Protocol):
    name: str

    def lookup(self, identity_key: str) -> OwnerLookup: ...

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome: ...


@dataclass(slots=True)
class CountingAdmitWrapper:
    """Test probe: counts remote admit calls per wrapped adapter."""

    inner: ActuatorAdapter
    admit_count: int = 0

    @property
    def name(self) -> str:
        return self.inner.name

    def lookup(self, identity_key: str) -> OwnerLookup:
        return self.inner.lookup(identity_key)

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        self.admit_count += 1
        return self.inner.admit(identity_key, **kwargs)


class AgentBusAdapter:
    """``libs/agent_bus_store/db/threads.py::create_thread`` + idempotency replay."""

    name = "agent_bus"

    def lookup(self, identity_key: str) -> OwnerLookup:
        from agent_bus_store.db.connection import connect
        from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

        with connect() as conn:
            thread_id = thread_id_for_idempotency_key(conn, identity_key)
        if thread_id is None:
            return OwnerLookup(found=False, durable=True)
        return OwnerLookup(
            found=True,
            actuator_ref=thread_id,
            state=ExecutionState.accepted,
            durable=True,
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        from agent_bus_store.db.threads import create_thread

        slug = str(kwargs.get("slug") or "execution-conformance")
        detail = create_thread(
            thread_id=kwargs.get("thread_id"),
            slug=slug,
            idempotency_key=identity_key,
        )
        if detail is None:
            raise RuntimeError("create_thread returned None for fresh id")
        thread_id = str(detail["id"])
        replay = bool(detail.get("idempotent_replay"))
        state = ExecutionState.accepted if replay else ExecutionState.accepted
        return AdmitOutcome(actuator_ref=thread_id, state=state)


class StargatePipelineAdapter:
    """In-process ``PipelineExecutionTracker`` (memory-only owner store)."""

    name = "stargate_pipeline"

    def __init__(self, tracker: Any | None = None) -> None:
        if tracker is None:
            from systems.pipeline.core.execution.async_tracker.tracker import (
                PipelineExecutionTracker,
            )

            tracker = PipelineExecutionTracker(event_bus=None)
        self._tracker = tracker

    @property
    def tracker(self) -> Any:
        return self._tracker

    def lookup(self, identity_key: str) -> OwnerLookup:
        record = self._tracker.get(identity_key)
        if record is None:
            return OwnerLookup(
                found=False,
                durable=False,
                note="PipelineExecutionTracker.records is in-process only",
            )
        status_map = {
            "running": ExecutionState.running,
            "completed": ExecutionState.completed,
            "failed": ExecutionState.failed,
        }
        state = status_map.get(record.status, ExecutionState.running)
        return OwnerLookup(
            found=True,
            actuator_ref=record.execution_id,
            state=state,
            durable=False,
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        from systems.pipeline.core.execution.async_tracker.constants import _utc_now_iso

        pipeline = str(kwargs.get("pipeline") or "conformance.probe")
        record = self._tracker.register_execution(
            execution_id=identity_key,
            pipeline=pipeline,
            started_at=_utc_now_iso(),
        )
        status_map = {
            "running": ExecutionState.running,
            "completed": ExecutionState.completed,
            "failed": ExecutionState.failed,
        }
        state = status_map.get(record.status, ExecutionState.running)
        return AdmitOutcome(actuator_ref=record.execution_id, state=state)


class CdpInflightAdapter:
    """``cdp_generate_inflight_ledger`` durable SQLite leg rows."""

    name = "cdp_ask"

    def lookup(self, identity_key: str) -> OwnerLookup:
        from systems.frontier_consult.cdp_generate_inflight_ledger import (
            read_inflight_leg,
        )

        leg = read_inflight_leg(identity_key)
        if leg is None:
            return OwnerLookup(found=False, durable=True)
        if leg.proof_emitted or leg.delivered:
            state = ExecutionState.completed
        elif leg.abandoned:
            state = ExecutionState.cancelled
        else:
            state = ExecutionState.running
        return OwnerLookup(
            found=True,
            actuator_ref=leg.execution_id,
            state=state,
            durable=True,
            note="proof_emitted=0 open leg is admitted but outcome may be unknown",
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        from systems.frontier_consult.cdp_generate_inflight_ledger import (
            read_inflight_leg,
            upsert_inflight_leg,
        )

        request_id = str(kwargs.get("request_id") or identity_key)
        thread_id = str(kwargs.get("thread_id") or "22398")
        upsert_inflight_leg(
            execution_id=identity_key,
            request_id=request_id,
            thread_id=thread_id,
            pointer_turn=int(kwargs.get("pointer_turn") or 1),
            caller_agent=kwargs.get("caller_agent"),
            prompt_uri=str(kwargs.get("prompt_uri") or "cortex://probe"),
            model_id=str(kwargs.get("model_id") or "probe"),
            max_wall_s=float(kwargs.get("max_wall_s") or 30.0),
        )
        leg = read_inflight_leg(identity_key)
        assert leg is not None
        return AdmitOutcome(
            actuator_ref=leg.execution_id, state=ExecutionState.accepted
        )


class CursorBridgeBusModelAdapter:
    """Thin bus-store model of cursor bridge MSG/REPLY (no MCP ``cursor_bridge`` import).

    ``services/mcp-server/tools/cursor_bridge.py`` ``_open_tab`` / ``_send_msg`` delegate
    to agent-bus sends; this adapter models ``send_msg`` as a durable MSG turn and treats
    a subsequent REPLY turn as terminal completion.
    """

    name = "cursor_bridge"

    def lookup(self, identity_key: str) -> OwnerLookup:
        from agent_bus_store.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT t.thread, t.turn_number, t.subject "
                "FROM turns t "
                "JOIN threads th ON th.id = t.thread "
                "WHERE t.subject = 'MSG' AND t.body LIKE ? "
                "ORDER BY t.id ASC LIMIT 1",
                (f"%intent:{identity_key}%",),
            ).fetchone()
            if row is None:
                return OwnerLookup(found=False, durable=True)
            thread_id = row["thread"]
            reply = conn.execute(
                "SELECT id FROM turns "
                "WHERE thread = ? AND subject = 'REPLY' AND turn_number > ? "
                "ORDER BY turn_number ASC LIMIT 1",
                (thread_id, row["turn_number"]),
            ).fetchone()
        if reply is None:
            return OwnerLookup(
                found=True,
                actuator_ref=thread_id,
                state=ExecutionState.running,
                durable=True,
                note="MSG durable without REPLY → blocked_unknown path",
            )
        return OwnerLookup(
            found=True,
            actuator_ref=thread_id,
            state=ExecutionState.completed,
            durable=True,
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        from agent_bus_store.db import create_thread, insert_turn

        thread_id = kwargs.get("thread_id")
        if thread_id is None:
            detail = create_thread(
                thread_id=None, slug=str(kwargs.get("slug") or "bridge-lane")
            )
            assert detail is not None
            thread_id = str(detail["id"])
        body = f"intent:{identity_key}\n{kwargs.get('body') or 'probe'}"
        insert_turn(
            thread=str(thread_id),
            from_agent=str(kwargs.get("from_agent") or "cursor-bridge"),
            to_agent=str(kwargs.get("to_agent") or "cursor-sdk"),
            subject="MSG",
            body=body,
        )
        return AdmitOutcome(actuator_ref=str(thread_id), state=ExecutionState.accepted)


def cdp_adapter_with_data_dir(data_dir: str) -> CdpInflightAdapter:
    os.environ["DATA_DIR"] = data_dir
    return CdpInflightAdapter()
