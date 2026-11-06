"""4 actuators × 8 capabilities — verified against on-disk sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CapabilityLevel = Literal["supported", "partial", "unsupported", "unknown"]


@dataclass(frozen=True, slots=True)
class ManifestCell:
    level: CapabilityLevel
    source: str
    fixture: str | None = None
    note: str | None = None


ActuatorName = Literal["agent_bus", "stargate_pipeline", "cdp_ask", "cursor_bridge"]
CapabilityName = Literal[
    "identity",
    "lookup",
    "accept_ack",
    "cancel",
    "dedup_retention",
    "fencing",
    "restart",
    "unknown_recovery",
]

CAPABILITIES: tuple[CapabilityName, ...] = (
    "identity",
    "lookup",
    "accept_ack",
    "cancel",
    "dedup_retention",
    "fencing",
    "restart",
    "unknown_recovery",
)

ACTUATORS: tuple[ActuatorName, ...] = (
    "agent_bus",
    "stargate_pipeline",
    "cdp_ask",
    "cursor_bridge",
)

# Sources re-verified in lane-22398 against repository paths (phase-4 densify).
MANIFEST: dict[ActuatorName, dict[CapabilityName, ManifestCell]] = {
    "agent_bus": {
        "identity": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/threads.py:create_thread:490",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
            note="Thread id + optional caller idempotency_key",
        ),
        "lookup": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/thread_idempotency.py:thread_id_for_idempotency_key:24",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
        ),
        "accept_ack": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/threads.py:create_thread:490",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
            note="Insert under write_connect; replay returns idempotent_replay detail",
        ),
        "cancel": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/threads.py:update_thread:591",
            fixture=None,
            note="Thread status/lifecycle close exists; no keyed cancel of in-flight admit",
        ),
        "dedup_retention": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/migrations/migration_014.py:migration_014:1",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
            note="UNIQUE partial index on threads.idempotency_key",
        ),
        "fencing": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/threads.py:create_thread:522",
            fixture=None,
            note="BEGIN IMMEDIATE serializes idempotency lookup vs insert",
        ),
        "restart": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/connection.py:_db_path:15",
            fixture="test_p4_c6_crash_after_reservation",
            note="AGENT_BUS_DB_PATH SQLite survives process restart",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/thread_idempotency.py:thread_id_for_idempotency_key:24",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
            note="Replay resolves unknown only when key hits durable row",
        ),
    },
    "stargate_pipeline": {
        "identity": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:PipelineExecutionTracker:28",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
            note="execution_id key in tracker.records",
        ),
        "lookup": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:get:122",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
        ),
        "accept_ack": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/lifecycle.py:register_execution:43",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
            note="Admits as status=running",
        ),
        "cancel": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/lifecycle.py:fail_execution:220",
            fixture=None,
            note="fail_execution terminal; no first-class cancel opcode",
        ),
        "dedup_retention": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/constants.py:_DEFAULT_MAX_RECORDS:14",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
            note="max_records=256, retention_seconds=86400 in-process eviction only",
        ),
        "fencing": ManifestCell(
            level="unsupported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:PipelineExecutionTracker:28",
            fixture=None,
            note="Dict writes without cross-process fencing",
        ),
        "restart": ManifestCell(
            level="unsupported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/constants.py:_DEFAULT_RETENTION_SECONDS:16",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
            note="Comment: Stargate restart invalidates in-process tracker",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:get:122",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
            note="After restart lookup miss → outcome_unknown; reconciler must not redispatch",
        ),
    },
    "cdp_ask": {
        "identity": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:cdp_inflight_leg:16",
            fixture="test_p4_c4_cdp_ledger_durable",
            note="execution_id PRIMARY KEY",
        ),
        "lookup": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:read_inflight_leg:196",
            fixture="test_p4_c4_cdp_ledger_durable",
        ),
        "accept_ack": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:upsert_inflight_leg:135",
            fixture="test_p4_c4_cdp_ledger_durable",
            note="Persist before worker spawn (module docstring AC12)",
        ),
        "cancel": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:mark_abandoned:248",
            fixture=None,
        ),
        "dedup_retention": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:cdp_inflight_leg:16",
            fixture="test_p4_c4_cdp_ledger_durable",
            note="SQLite WAL under DATA_DIR",
        ),
        "fencing": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:try_claim_proof_publish:261",
            fixture=None,
            note="Finalize claim lease; admit path is upsert OR REPLACE",
        ),
        "restart": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:_db_path:52",
            fixture="test_p4_c4_cdp_ledger_durable",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:read_inflight_leg:196",
            fixture="test_p4_c4_cdp_open_without_proof",
            note="Open leg without proof_emitted → outcome_unknown; no blind retry",
        ),
    },
    "cursor_bridge": {
        "identity": ManifestCell(
            level="partial",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg:94",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="Modeled via MSG turn body intent: tag; MCP runtime not imported in conformance layer",
        ),
        "lookup": ManifestCell(
            level="partial",
            source="libs/execution_conformance/adapters.py:CursorBridgeBusModelAdapter.lookup:1",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="Bus-store scan for MSG/REPLY pairing",
        ),
        "accept_ack": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/turns.py:insert_turn:218",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="Durable MSG turn is accept surrogate",
        ),
        "cancel": ManifestCell(
            level="unknown",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg:94",
            fixture=None,
            note="No keyed cancel in bridge tool; would require bus protocol extension",
        ),
        "dedup_retention": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/turns.py:insert_turn:218",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="Turn rows durable; bridge layer does not expose idempotency_key on MSG",
        ),
        "fencing": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/connection.py:write_connect:1",
            fixture=None,
            note="SQLite writer serialization only",
        ),
        "restart": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/connection.py:_db_path:15",
            fixture="test_p4_c5_bridge_msg_without_reply",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="libs/execution_conformance/adapters.py:CursorBridgeBusModelAdapter.lookup:1",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="MSG without REPLY → blocked_unknown; no resend",
        ),
    },
}


def manifest_unknowns() -> list[tuple[ActuatorName, CapabilityName, ManifestCell]]:
    out: list[tuple[ActuatorName, CapabilityName, ManifestCell]] = []
    for actuator in ACTUATORS:
        for cap in CAPABILITIES:
            cell = MANIFEST[actuator][cap]
            if cell.level == "unknown":
                out.append((actuator, cap, cell))
    return out


def assert_manifest_complete() -> None:
    for actuator in ACTUATORS:
        for cap in CAPABILITIES:
            cell = MANIFEST[actuator].get(cap)
            if cell is None:
                raise AssertionError(f"missing manifest cell {actuator}.{cap}")
            if not cell.source:
                raise AssertionError(f"missing source for {actuator}.{cap}")


def owner_storage_durable(actuator: ActuatorName) -> bool:
    restart = MANIFEST[actuator]["restart"]
    return restart.level in {"supported", "partial"}


def owner_storage_memory_only(actuator: ActuatorName) -> bool:
    restart = MANIFEST[actuator]["restart"]
    return restart.level == "unsupported"
