"""Read-only wrappers over existing actuator surfaces (no MCP runtime import)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .states import ExecutionState
from .store_epoch import file_store_epoch, intent_tag_body, intent_tag_length

# PipelineExecutionRecord.status (records.py) is only running | completed | failed.
_TRACKER_STATUS_MAP: dict[str, ExecutionState] = {
    "running": ExecutionState.running,
    "completed": ExecutionState.completed,
    "failed": ExecutionState.failed,
}


def map_tracker_status(status: str) -> ExecutionState:
    return _TRACKER_STATUS_MAP.get(status, ExecutionState.outcome_unknown)


@dataclass(frozen=True, slots=True)
class OwnerLookup:
    found: bool
    actuator_ref: str | None = None
    state: ExecutionState | None = None
    store_epoch: str = ""
    unknown_reason: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AdmitOutcome:
    actuator_ref: str
    state: ExecutionState
    idempotent_replay: bool = False


class ActuatorAdapter(Protocol):
    name: str
    admit_idempotent: bool

    def store_epoch(self) -> str: ...

    def health(self) -> bool: ...

    def lookup(self, identity_key: str) -> OwnerLookup: ...

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome: ...


@dataclass(slots=True)
class CountingAdmitWrapper:
    inner: ActuatorAdapter
    admit_count: int = 0

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def admit_idempotent(self) -> bool:
        return self.inner.admit_idempotent

    def store_epoch(self) -> str:
        return self.inner.store_epoch()

    def health(self) -> bool:
        return self.inner.health()

    def lookup(self, identity_key: str) -> OwnerLookup:
        return self.inner.lookup(identity_key)

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        self.admit_count += 1
        return self.inner.admit(identity_key, **kwargs)


def agent_bus_store_path() -> Path:
    from agent_bus_store.db.connection import _db_path

    return Path(_db_path()).expanduser()


def cdp_store_path() -> Path:
    from systems.frontier_consult.cdp_generate_inflight_ledger import _db_path

    return _db_path()


def _validate_idempotency_key(key: str) -> None:
    if len(key) < 8 or len(key) > 128:
        raise ValueError("identity_key must be 8..128 chars for agent_bus idempotency")


class AgentBusAdapter:
    name = "agent_bus"
    admit_idempotent = True

    def store_epoch(self) -> str:
        return file_store_epoch(agent_bus_store_path())

    def health(self) -> bool:
        return agent_bus_store_path().parent.exists()

    def lookup(self, identity_key: str) -> OwnerLookup:
        from agent_bus_store.db.connection import connect
        from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

        epoch = self.store_epoch()
        with connect() as conn:
            thread_id = thread_id_for_idempotency_key(conn, identity_key)
        if thread_id is None:
            return OwnerLookup(found=False, store_epoch=epoch)
        return OwnerLookup(
            found=True,
            actuator_ref=thread_id,
            state=ExecutionState.accepted,
            store_epoch=epoch,
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        from agent_bus_store.db.threads import create_thread

        _validate_idempotency_key(identity_key)
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
        return AdmitOutcome(
            actuator_ref=thread_id,
            state=ExecutionState.accepted,
            idempotent_replay=replay,
        )


class StargatePipelineAdapter:
    name = "stargate_pipeline"
    admit_idempotent = False

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

    def store_epoch(self) -> str:
        return f"memory:{id(self._tracker)}"

    def health(self) -> bool:
        return True

    def lookup(self, identity_key: str) -> OwnerLookup:
        record = self._tracker.get(identity_key)
        epoch = self.store_epoch()
        if record is None:
            return OwnerLookup(
                found=False,
                store_epoch=epoch,
                note="PipelineExecutionTracker.records is in-process only",
            )
        return OwnerLookup(
            found=True,
            actuator_ref=record.execution_id,
            state=map_tracker_status(record.status),
            store_epoch=epoch,
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        existing = self.lookup(identity_key)
        if existing.found and existing.actuator_ref:
            return AdmitOutcome(
                actuator_ref=existing.actuator_ref,
                state=existing.state or ExecutionState.running,
                idempotent_replay=True,
            )
        from systems.pipeline.core.execution.async_tracker.constants import _utc_now_iso

        pipeline = str(kwargs.get("pipeline") or "conformance.probe")
        record = self._tracker.register_execution(
            execution_id=identity_key,
            pipeline=pipeline,
            started_at=_utc_now_iso(),
        )
        return AdmitOutcome(
            actuator_ref=record.execution_id,
            state=map_tracker_status(record.status),
        )


class CdpInflightAdapter:
    name = "cdp_ask"
    admit_idempotent = False

    def store_epoch(self) -> str:
        return file_store_epoch(cdp_store_path())

    def health(self) -> bool:
        return cdp_store_path().parent.exists()

    def lookup(self, identity_key: str) -> OwnerLookup:
        from systems.frontier_consult.cdp_generate_inflight_ledger import (
            read_inflight_leg,
        )

        epoch = self.store_epoch()
        leg = read_inflight_leg(identity_key)
        if leg is None:
            return OwnerLookup(found=False, store_epoch=epoch)
        if leg.proof_emitted or leg.delivered:
            return OwnerLookup(
                found=True,
                actuator_ref=leg.execution_id,
                state=ExecutionState.completed,
                store_epoch=epoch,
            )
        if leg.abandoned:
            return OwnerLookup(
                found=True,
                actuator_ref=leg.execution_id,
                state=ExecutionState.outcome_unknown,
                store_epoch=epoch,
                unknown_reason="abandoned_worker_may_run",
            )
        return OwnerLookup(
            found=True,
            actuator_ref=leg.execution_id,
            state=ExecutionState.accepted,
            store_epoch=epoch,
            unknown_reason="open_leg_without_board_proof",
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
        if leg is None:
            raise RuntimeError("upsert_inflight_leg did not persist")
        return AdmitOutcome(
            actuator_ref=leg.execution_id, state=ExecutionState.accepted
        )


class CursorBridgeBusModelAdapter:
    name = "cursor_bridge"
    admit_idempotent = False

    def store_epoch(self) -> str:
        return file_store_epoch(agent_bus_store_path())

    def health(self) -> bool:
        return agent_bus_store_path().parent.exists()

    def lookup(self, identity_key: str) -> OwnerLookup:
        from agent_bus_store.db.connection import connect

        epoch = self.store_epoch()
        tag = intent_tag_body(identity_key)
        tag_len = intent_tag_length(identity_key)
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM turns "
                "WHERE subject = 'MSG' AND substr(body, 1, ?) = ?",
                (tag_len, tag),
            ).fetchone()[0]
            if count == 0:
                return OwnerLookup(found=False, store_epoch=epoch)
            if count > 1:
                return OwnerLookup(
                    found=True,
                    state=ExecutionState.outcome_unknown,
                    store_epoch=epoch,
                    unknown_reason="duplicate_msg",
                )
            row = conn.execute(
                "SELECT thread, turn_number FROM turns "
                "WHERE subject = 'MSG' AND substr(body, 1, ?) = ? "
                "ORDER BY id ASC LIMIT 1",
                (tag_len, tag),
            ).fetchone()
            if row is None:
                return OwnerLookup(found=False, store_epoch=epoch)
            thread_id = row["thread"]
            reply = conn.execute(
                "SELECT id FROM turns "
                "WHERE thread = ? AND subject = 'REPLY' "
                "AND substr(body, 1, ?) = ? LIMIT 1",
                (thread_id, tag_len, tag),
            ).fetchone()
        if reply is None:
            return OwnerLookup(
                found=True,
                actuator_ref=thread_id,
                state=ExecutionState.accepted,
                store_epoch=epoch,
                unknown_reason="msg_without_correlated_reply",
            )
        return OwnerLookup(
            found=True,
            actuator_ref=thread_id,
            state=ExecutionState.completed,
            store_epoch=epoch,
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        from agent_bus_store.db import create_thread, insert_turn

        thread_id = kwargs.get("thread_id")
        if thread_id is None:
            detail = create_thread(
                thread_id=None, slug=str(kwargs.get("slug") or "bridge-lane")
            )
            if detail is None:
                raise RuntimeError("create_thread failed")
            thread_id = str(detail["id"])
        body = intent_tag_body(identity_key) + str(kwargs.get("body") or "probe")
        insert_turn(
            thread=str(thread_id),
            from_agent=str(kwargs.get("from_agent") or "cursor-bridge"),
            to_agent=str(kwargs.get("to_agent") or "cursor-sdk"),
            subject="MSG",
            body=body,
        )
        return AdmitOutcome(actuator_ref=str(thread_id), state=ExecutionState.accepted)


class HealthOnlyAdapter:
    name = "health_probe"
    admit_idempotent = False

    def store_epoch(self) -> str:
        return "health-probe:fixed"

    def health(self) -> bool:
        return True

    def lookup(self, identity_key: str) -> OwnerLookup:
        return OwnerLookup(found=False, store_epoch=self.store_epoch())

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        raise RuntimeError("health probe does not admit")
