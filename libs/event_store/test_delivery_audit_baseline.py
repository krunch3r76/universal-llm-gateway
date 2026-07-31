"""Baseline harness tests for delivery-audit token-locality campaigns."""

from __future__ import annotations

from pathlib import Path

import pytest

from event_store.delivery_audit_baseline import (
    BaselineArtifact,
    BaselineTrace,
    fetch_workflow_summaries,
    record_baseline_trace,
    summarize_baseline_campaign,
)
from event_store.delivery_audit_registry import (
    connect,
    ensure_schema,
    list_artifacts_for_audit,
)


@pytest.fixture
def registry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db_path = tmp_path / "delivery-audit.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.commit()
    return db_path


def _trace(
    *,
    workflow_class: str = "simple_edit",
    execution_id: str = "exec-1",
    tokens: int = 100,
    campaign_id: str = "campaign-baseline",
) -> BaselineTrace:
    return BaselineTrace(
        workflow_class=workflow_class,
        phase="baseline",
        campaign_id=campaign_id,
        seat_substrate="cursor",
        execution_id=execution_id,
        artifacts=(
            BaselineArtifact(
                artifact_class="boot_card_block",
                artifact_id="Agent Skills",
                body="resident boot card",
                fetch_scope="resident",
                token_category="resident_guidance",
                delivered_tokens=20,
            ),
            BaselineArtifact(
                artifact_class="http_skill_body",
                artifact_id="agent-skills/consult-routing.md#Implement lane",
                body="fetched guidance",
                fetch_scope="section",
                token_category="fetched_guidance",
                delivered_tokens=tokens,
            ),
            BaselineArtifact(
                artifact_class="http_skill_body",
                artifact_id="cortex://agent-skills/consult-routing.md#Implement lane",
                body="duplicate guidance",
                fetch_scope="section",
                token_category="fetched_guidance",
                delivered_tokens=10,
            ),
            BaselineArtifact(
                artifact_class="tool_fol_descriptor",
                artifact_id="cortex",
                body="schema",
                tool_surface="mcp_schema",
                fetch_scope="section",
                token_category="tool_schema_discovery",
                delivered_tokens=5,
            ),
        ),
    )


def test_record_baseline_trace_writes_artifacts_and_summary(registry_db: Path) -> None:
    result = record_baseline_trace(_trace(), db_path=registry_db)

    rows = list_artifacts_for_audit(result["audit_id"], db_path=registry_db)
    summaries = fetch_workflow_summaries(
        campaign_id="campaign-baseline",
        phase="baseline",
        db_path=registry_db,
    )

    assert result["artifact_count"] == 4
    assert [row["guidance_resource_key"] for row in rows] == [
        "guidance:boot-card#agent-skills",
        "guidance:consult-routing#implement-lane",
        "guidance:consult-routing#implement-lane",
        "guidance:mcp-schema#cortex",
    ]
    assert [row["is_duplicate"] for row in rows] == [0, 0, 1, 0]
    assert len(summaries) == 1
    assert summaries[0]["workflow_class"] == "simple_edit"
    assert summaries[0]["input_tokens"] == 135
    assert summaries[0]["duplicate_guidance_tokens"] == 10
    assert summaries[0]["trigger_fan_in_count"] == 2


def test_record_baseline_trace_rejects_unknown_workflow_class(
    registry_db: Path,
) -> None:
    trace = _trace(workflow_class="unknown")

    with pytest.raises(ValueError, match="unknown workflow_class"):
        record_baseline_trace(trace, db_path=registry_db)


def test_summarize_baseline_campaign_reports_percentiles_and_caveat(
    registry_db: Path,
) -> None:
    for index, tokens in enumerate((10, 20, 30, 40), start=1):
        record_baseline_trace(
            _trace(execution_id=f"exec-{index}", tokens=tokens),
            db_path=registry_db,
        )

    report = summarize_baseline_campaign(
        "campaign-baseline",
        phase="baseline",
        db_path=registry_db,
    )

    assert report["trace_count"] == 4
    assert report["workflow_group_count"] == 1
    summary = report["workflow_summaries"][0]
    assert summary["sample_count"] == 4
    assert summary["meets_milestone1_minimum"] is False
    assert summary["p50_input_tokens"] == 55
    assert summary["p95_input_tokens"] == 75
    assert summary["p95_caveat"] == "wide_interval_n_lt_50"
    assert summary["p50_token_vector"]["fetched_guidance_tokens"] == 30
    assert summary["p95_token_vector"]["fetched_guidance_tokens"] == 50
