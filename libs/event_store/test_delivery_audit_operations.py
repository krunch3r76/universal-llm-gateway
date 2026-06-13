"""Read-through operation and recent-failures tests for B3 delivery audit."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from event_store.delivery_audit_registry import (
    REGISTRY_SCHEMA_VERSION,
    artifact_identity_key,
    connect,
    ensure_schema,
    new_artifact_record_id,
    new_audit_id,
)
from event_store.operation_catalog import get_operation, list_operations
from event_store.operation_dispatch import execute_operation
from event_store.operations_delivery_audit import (
    _delivery_audit_artifacts,
    _delivery_audit_baseline_campaign,
    _delivery_audit_parent,
    _delivery_audit_token_rollup,
)
from event_store.operations_impl import _recent_failures
from event_store.store import EventStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def registry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db_path = tmp_path / "delivery-audit.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.commit()
    return db_path


def _seed_parent_and_children(db_path: Path) -> tuple[str, str]:
    audit_id = new_audit_id()
    execution_id = "exec-b3-1"
    now = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO delivery_audits (
                audit_id, execution_id, audit_opened_at, aggregate_audit_status,
                registry_schema_version, audit_policy_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                execution_id,
                now,
                "unaudited",
                REGISTRY_SCHEMA_VERSION,
                "policy-v1",
                now,
                now,
            ),
        )
        for sequence, artifact_id in enumerate(
            (
                "agent-skills/consult-routing.md#Implement lane",
                "agent-skills/dispatch-shape.md#arguments",
            ),
            start=1,
        ):
            identity = artifact_identity_key(
                audit_id=audit_id,
                artifact_class="http_skill_body",
                artifact_id=artifact_id,
                delivery_step="dispatch_packet",
                recipient_scope="cursor-sdk",
            )
            conn.execute(
                """
                INSERT INTO delivered_artifacts (
                    artifact_record_id, audit_id, artifact_sequence,
                    artifact_identity_key, artifact_class, artifact_id,
                    delivery_step, recipient_scope, audit_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_artifact_record_id(),
                    audit_id,
                    sequence,
                    identity,
                    "http_skill_body",
                    artifact_id,
                    "dispatch_packet",
                    "cursor-sdk",
                    "unaudited",
                    now,
                    now,
                ),
            )
        conn.commit()
    return audit_id, execution_id


@pytest.mark.asyncio
async def test_delivery_audit_parent_by_audit_id(registry_db: Path) -> None:
    audit_id, _ = _seed_parent_and_children(registry_db)
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_parent({"audit_id": audit_id}, store)
    finally:
        await store.close()

    assert body["lookup_key"] == "audit_id"
    assert body["lookup_value"] == audit_id
    assert body["parent"]["audit_id"] == audit_id


@pytest.mark.asyncio
async def test_delivery_audit_parent_by_execution_id(registry_db: Path) -> None:
    audit_id, execution_id = _seed_parent_and_children(registry_db)
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_parent({"execution_id": execution_id}, store)
    finally:
        await store.close()

    assert body["lookup_key"] == "execution_id"
    assert body["parent"]["audit_id"] == audit_id


@pytest.mark.asyncio
async def test_delivery_audit_parent_requires_exactly_one_lookup_key() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_parent({}, store)
    finally:
        await store.close()

    assert "error" in body


@pytest.mark.asyncio
async def test_delivery_audit_artifacts_ordered_by_sequence(registry_db: Path) -> None:
    audit_id, _ = _seed_parent_and_children(registry_db)
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_artifacts({"audit_id": audit_id}, store)
    finally:
        await store.close()

    assert body["count"] == 2
    assert [row["artifact_sequence"] for row in body["artifacts"]] == [1, 2]
    assert body["artifacts"][0]["artifact_id"].endswith("#Implement lane")


def _seed_parent_with_token_rows(db_path: Path) -> str:
    audit_id = new_audit_id()
    now = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO delivery_audits (
                audit_id, execution_id, audit_opened_at, aggregate_audit_status,
                registry_schema_version, audit_policy_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                "exec-token-1",
                now,
                "unaudited",
                REGISTRY_SCHEMA_VERSION,
                "policy-v1",
                now,
                now,
            ),
        )
        token_rows = [
            (
                1,
                "resident",
                "resident_guidance",
                100,
                400,
                "shared-key",
                0,
                None,
            ),
            (
                2,
                "section",
                "fetched_guidance",
                50,
                800,
                "shared-key",
                1,
                5,
            ),
            (
                3,
                "whole_doc",
                "tool_schema_discovery",
                None,
                None,
                None,
                None,
                None,
            ),
        ]
        for (
            sequence,
            fetch_scope,
            token_category,
            tokens,
            nbytes,
            key,
            dup,
            overlap,
        ) in token_rows:
            identity = artifact_identity_key(
                audit_id=audit_id,
                artifact_class="http_skill_body",
                artifact_id=f"agent-skills/skill-{sequence}.md#section",
                delivery_step="dispatch_packet",
                recipient_scope="cursor-sdk",
            )
            conn.execute(
                """
                INSERT INTO delivered_artifacts (
                    artifact_record_id, audit_id, artifact_sequence,
                    artifact_identity_key, artifact_class, artifact_id,
                    delivery_step, recipient_scope, audit_status,
                    guidance_resource_key, delivered_tokens, fetch_scope,
                    token_category, delivered_bytes, is_duplicate,
                    restated_overlap_tokens, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_artifact_record_id(),
                    audit_id,
                    sequence,
                    identity,
                    "http_skill_body",
                    f"agent-skills/skill-{sequence}.md#section",
                    "dispatch_packet",
                    "cursor-sdk",
                    "unaudited",
                    key,
                    tokens,
                    fetch_scope,
                    token_category,
                    nbytes,
                    dup,
                    overlap,
                    now,
                    now,
                ),
            )
        conn.commit()
    return audit_id


def _seed_workflow_summary(db_path: Path, *, campaign_id: str) -> None:
    audit_id = new_audit_id()
    execution_id = "exec-baseline-1"
    now = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO delivery_audits (
                audit_id, execution_id, audit_opened_at, aggregate_audit_status,
                registry_schema_version, audit_policy_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                execution_id,
                now,
                "unaudited",
                REGISTRY_SCHEMA_VERSION,
                "token-locality-baseline-v1",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO guidance_workflow_summaries (
                workflow_summary_id, audit_id, execution_id, workflow_class,
                phase, campaign_id, seat_substrate, input_tokens,
                resident_guidance_tokens, fetched_guidance_tokens,
                duplicate_guidance_tokens, tool_schema_tokens, task_corpus_tokens,
                transcript_restated_tokens, restated_overlap_tokens_total,
                whole_doc_bytes, section_bytes, trigger_fan_in_count,
                artifact_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "summary-1",
                audit_id,
                execution_id,
                "debugging",
                "baseline",
                campaign_id,
                "cursor",
                123,
                10,
                20,
                0,
                3,
                90,
                0,
                0,
                0,
                200,
                1,
                3,
                now,
                now,
            ),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_delivery_audit_token_rollup(registry_db: Path) -> None:
    audit_id = _seed_parent_with_token_rows(registry_db)
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_token_rollup({"audit_id": audit_id}, store)
    finally:
        await store.close()

    assert body["count"] == 3
    assert body["audit_id"] == audit_id
    assert body["rollup"] == {
        "resident_guidance_tokens": 100,
        "fetched_guidance_tokens": 50,
        "duplicate_guidance_tokens": 50,
        "tool_schema_tokens": 0,
        "task_corpus_tokens": 0,
        "transcript_restated_tokens": 0,
        "restated_overlap_tokens_total": 5,
        "whole_doc_bytes": 0,
        "section_bytes": 800,
        "trigger_fan_in_count": 2,
    }


@pytest.mark.asyncio
async def test_delivery_audit_token_rollup_requires_audit_id() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_token_rollup({}, store)
    finally:
        await store.close()

    assert body == {"error": "audit_id is required"}


@pytest.mark.asyncio
async def test_delivery_audit_baseline_campaign(registry_db: Path) -> None:
    _seed_workflow_summary(registry_db, campaign_id="campaign-1")
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_baseline_campaign(
            {"campaign_id": "campaign-1"},
            store,
        )
    finally:
        await store.close()

    assert body["trace_count"] == 1
    assert body["workflow_summaries"][0]["workflow_class"] == "debugging"
    assert body["workflow_summaries"][0]["p95_caveat"] == "wide_interval_n_lt_50"


@pytest.mark.asyncio
async def test_delivery_audit_baseline_campaign_requires_campaign_id() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await _delivery_audit_baseline_campaign({}, store)
    finally:
        await store.close()

    assert body == {"error": "campaign_id is required"}


def test_operation_catalog_includes_delivery_audit_operations() -> None:
    names = {op["name"] for op in list_operations()}
    assert "delivery-audit-parent" in names
    assert "delivery-audit-artifacts" in names
    assert "delivery-audit-token-rollup" in names
    assert "delivery-audit-baseline-campaign" in names
    assert get_operation("delivery-audit-parent") is not None
    assert get_operation("delivery-audit-artifacts") is not None
    assert get_operation("delivery-audit-token-rollup") is not None
    assert get_operation("delivery-audit-baseline-campaign") is not None


@pytest.mark.asyncio
async def test_execute_operation_dispatches_delivery_audit_parent(
    registry_db: Path,
) -> None:
    audit_id, _ = _seed_parent_and_children(registry_db)
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await execute_operation(
            "delivery-audit-parent",
            {"audit_id": audit_id},
            store,
        )
    finally:
        await store.close()

    assert body["parent"]["audit_id"] == audit_id


@pytest.mark.asyncio
async def test_execute_operation_dispatches_delivery_audit_baseline_campaign(
    registry_db: Path,
) -> None:
    _seed_workflow_summary(registry_db, campaign_id="campaign-1")
    store = EventStore(":memory:")
    await store.open()
    try:
        body = await execute_operation(
            "delivery-audit-baseline-campaign",
            {"campaign_id": "campaign-1"},
            store,
        )
    finally:
        await store.close()

    assert body["trace_count"] == 1


def test_mcp_events_allowlist_accepts_delivery_audit_operations() -> None:
    from pathlib import Path

    events_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "mcp-server"
        / "tools"
        / "events.py"
    )
    tree = ast.parse(events_path.read_text(encoding="utf-8"))
    valid_operations: set[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_VALID_OPERATIONS":
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if value.func.id == "frozenset" and value.args:
                        valid_operations = set(ast.literal_eval(value.args[0]))
                break
    assert isinstance(valid_operations, set)
    assert "delivery-audit-parent" in valid_operations
    assert "delivery-audit-artifacts" in valid_operations
    assert "delivery-audit-token-rollup" in valid_operations
    assert "delivery-audit-baseline-campaign" in valid_operations


def test_recent_failures_includes_delivery_audit_registry_write_failed() -> None:
    async def _run() -> dict[str, Any]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "delivery.audit.registry.write.failed",
                        "role": "observation",
                        "scope": "node",
                        "ts_unix_ms": 1000,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "source": "test",
                        "payload": {
                            "audit_id": "audit-1",
                            "error_code": "sqlite_busy",
                        },
                    },
                    {
                        "signal": "delivery.audit.parent.opened",
                        "role": "observation",
                        "scope": "node",
                        "ts_unix_ms": 2000,
                        "timestamp": "2026-01-01T00:00:02Z",
                        "source": "test",
                        "payload": {"audit_id": "audit-1"},
                    },
                ]
            )
            return await _recent_failures({"limit": 10, "since_ts": 0}, store)
        finally:
            await store.close()

    body = asyncio.run(_run())

    assert body["count"] == 1
    assert body["rows"][0]["signal"] == "delivery.audit.registry.write.failed"
