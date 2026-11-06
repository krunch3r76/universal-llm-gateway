"""4 actuators × 8 capabilities — verified against on-disk sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_DIR = Path(__file__).resolve().parent / "tests"

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

MANIFEST: dict[ActuatorName, dict[CapabilityName, ManifestCell]] = {
    "agent_bus": {
        "identity": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/threads.py:create_thread",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
        ),
        "lookup": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/thread_idempotency.py:thread_id_for_idempotency_key",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
        ),
        "accept_ack": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/threads.py:create_thread",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
        ),
        "cancel": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/threads.py:update_thread",
            fixture=None,
            note="Thread close; no keyed cancel of in-flight admit",
        ),
        "dedup_retention": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/migrations/migration_014.py:migration_014",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
            note="UNIQUE idempotency_key; delete_thread ends dedup scope",
        ),
        "fencing": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/threads.py:create_thread",
            fixture=None,
        ),
        "restart": ManifestCell(
            level="supported",
            source="libs/agent_bus_store/db/connection.py:_db_path",
            fixture="test_p4_c6_crash_after_reservation",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/thread_idempotency.py:thread_id_for_idempotency_key",
            fixture="test_p4_c2_agent_bus_idempotency_reconcile",
        ),
    },
    "stargate_pipeline": {
        "identity": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:PipelineExecutionTracker",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
            note="Class accepts caller execution_id; service route may mint server-side",
        ),
        "lookup": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:get",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
        ),
        "accept_ack": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/lifecycle.py:register_execution",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
        ),
        "cancel": ManifestCell(
            level="unsupported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/lifecycle.py:fail_execution",
            fixture=None,
            note="fail_execution is not cancel",
        ),
        "dedup_retention": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/constants.py:_DEFAULT_MAX_RECORDS",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
        ),
        "fencing": ManifestCell(
            level="unsupported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:PipelineExecutionTracker",
            fixture=None,
        ),
        "restart": ManifestCell(
            level="unsupported",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/constants.py:_DEFAULT_RETENTION_SECONDS",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/pipeline/core/execution/async_tracker/tracker.py:get",
            fixture="test_p4_c3_stargate_tracker_restart_unknown",
        ),
    },
    "cdp_ask": {
        "identity": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:upsert_inflight_leg",
            fixture="test_p4_c4_cdp_ledger_durable",
            note="execution_id PRIMARY KEY in cdp_inflight_leg DDL",
        ),
        "lookup": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:read_inflight_leg",
            fixture="test_p4_c4_cdp_ledger_durable",
        ),
        "accept_ack": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:upsert_inflight_leg",
            fixture="test_p4_c4_cdp_ledger_durable",
            note="Local durable intent before worker spawn, not remote owner ack",
        ),
        "cancel": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:mark_abandoned",
            fixture="test_p4_regression_cdp_abandoned_cancelled",
            note="Ledger abandon row; does not stop worker/tab",
        ),
        "dedup_retention": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:upsert_inflight_leg",
            fixture="test_p4_c4_cdp_ledger_durable",
            note="INSERT OR REPLACE overwrites; DELETE only in test helpers",
        ),
        "fencing": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:try_claim_proof_publish",
            fixture=None,
        ),
        "restart": ManifestCell(
            level="supported",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:_db_path",
            fixture="test_p4_c4_cdp_ledger_durable",
        ),
        "unknown_recovery": ManifestCell(
            level="partial",
            source="services/universal-stargate/systems/frontier_consult/cdp_generate_inflight_ledger.py:read_inflight_leg",
            fixture="test_p4_c4_cdp_open_without_proof",
        ),
    },
    "cursor_bridge": {
        "identity": ManifestCell(
            level="unknown",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg",
            fixture=None,
            note="Real bridge has no intent tag; modeled_by CursorBridgeBusModelAdapter",
        ),
        "lookup": ManifestCell(
            level="unknown",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="modeled_by CursorBridgeBusModelAdapter in conformance tests",
        ),
        "accept_ack": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/turns.py:insert_turn",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="Bus MSG turn models bridge send_msg durability",
        ),
        "cancel": ManifestCell(
            level="unknown",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg",
            fixture=None,
        ),
        "dedup_retention": ManifestCell(
            level="unsupported",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="No bridge idempotency key on MSG",
        ),
        "fencing": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/connection.py:write_connect",
            fixture=None,
        ),
        "restart": ManifestCell(
            level="partial",
            source="libs/agent_bus_store/db/connection.py:_db_path",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="Bus SQLite durability; bridge tool itself is stateless",
        ),
        "unknown_recovery": ManifestCell(
            level="unknown",
            source="services/mcp-server/tools/cursor_bridge.py:_send_msg",
            fixture="test_p4_c5_bridge_msg_without_reply",
            note="modeled_by conformance bus adapter",
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


def _resolve_source(source: str) -> None:
    path_str, _, symbol_part = source.partition(":")
    path = _REPO_ROOT / path_str
    if not path.is_file():
        raise AssertionError(f"manifest source missing file: {path}")
    if not symbol_part:
        return
    symbol = symbol_part.split(":")[0]
    text = path.read_text(encoding="utf-8", errors="replace")
    if symbol not in text:
        raise AssertionError(f"manifest symbol {symbol!r} not found in {path}")


def _fixture_collected(name: str) -> bool:
    for path in _TESTS_DIR.glob("test_p4_*.py"):
        content = path.read_text(encoding="utf-8", errors="replace")
        if f"def {name}" in content:
            return True
    return False


def assert_manifest_complete() -> None:
    for actuator in ACTUATORS:
        for cap in CAPABILITIES:
            cell = MANIFEST[actuator].get(cap)
            if cell is None:
                raise AssertionError(f"missing manifest cell {actuator}.{cap}")
            if not cell.source:
                raise AssertionError(f"missing source for {actuator}.{cap}")
            _resolve_source(cell.source)
            if cell.level == "supported" and cell.fixture is None:
                raise AssertionError(
                    f"supported cell {actuator}.{cap} requires fixture"
                )
            if cell.fixture is not None and not _fixture_collected(cell.fixture):
                raise AssertionError(
                    f"fixture {cell.fixture!r} not found for {actuator}.{cap}"
                )
