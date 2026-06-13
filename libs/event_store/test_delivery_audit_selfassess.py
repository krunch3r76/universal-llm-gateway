"""Unit tests for delivery-audit cross-seat self-assessment rubric scoring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from event_store.delivery_audit_registry import (
    REGISTRY_SCHEMA_VERSION,
    connect,
    ensure_schema,
    new_audit_id,
)
from event_store.delivery_audit_selfassess import (
    render_selfassess_closeout,
    score_selfassess_rubric,
)


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


def _insert_summary(
    db_path: Path,
    *,
    summary_id: str,
    campaign_id: str,
    workflow_class: str = "debugging",
    seat_substrate: str = "cursor",
    resident: int = 10,
    fetched: int = 100,
    duplicate: int = 5,
    tool_schema: int = 3,
    task_corpus: int = 20,
    restated: int = 0,
    restated_overlap_total: int = 0,
    whole_doc_bytes: int = 100,
    section_bytes: int = 900,
    artifact_count: int = 2,
    trigger_fan_in: int = 1,
) -> None:
    audit_id = new_audit_id()
    execution_id = f"exec-{summary_id}"
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
                summary_id,
                audit_id,
                execution_id,
                workflow_class,
                "baseline",
                campaign_id,
                seat_substrate,
                resident + fetched + tool_schema + task_corpus + restated,
                resident,
                fetched,
                duplicate,
                tool_schema,
                task_corpus,
                restated,
                restated_overlap_total,
                whole_doc_bytes,
                section_bytes,
                trigger_fan_in,
                artifact_count,
                now,
                now,
            ),
        )
        conn.commit()


def _group(report: dict) -> dict:
    assert report["group_count"] == 1
    return report["rubric_groups"][0]


def test_all_met_slice(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-all-met",
        campaign_id="campaign-all-met",
        fetched=100,
        duplicate=5,
        section_bytes=900,
        whole_doc_bytes=100,
    )
    group = _group(
        score_selfassess_rubric("campaign-all-met", db_path=registry_db),
    )
    dims = group["dimensions"]
    assert dims["guidance_locality"]["verdict"] == "met"
    assert dims["duplicate_guidance"]["verdict"] == "met"
    assert dims["restatement_discipline"]["verdict"] == "met"
    assert dims["restatement_discipline"]["evidence"]["detector_unlanded"] is True
    assert dims["delivery_auditability"]["verdict"] == "met"


def test_duplicate_unmet(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-dup-unmet",
        campaign_id="campaign-dup-unmet",
        fetched=100,
        duplicate=30,
    )
    dims = _group(
        score_selfassess_rubric("campaign-dup-unmet", db_path=registry_db),
    )["dimensions"]
    assert dims["duplicate_guidance"]["verdict"] == "unmet"
    assert dims["duplicate_guidance"]["evidence"]["dup_ratio"] == pytest.approx(0.30)


def test_locality_partial_and_unmet(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-locality-partial",
        campaign_id="campaign-locality",
        section_bytes=600,
        whole_doc_bytes=400,
    )
    partial_dims = _group(
        score_selfassess_rubric("campaign-locality", db_path=registry_db),
    )["dimensions"]
    assert partial_dims["guidance_locality"]["verdict"] == "partial"

    _insert_summary(
        registry_db,
        summary_id="summary-locality-unmet",
        campaign_id="campaign-locality-unmet",
        section_bytes=100,
        whole_doc_bytes=900,
    )
    unmet_dims = _group(
        score_selfassess_rubric("campaign-locality-unmet", db_path=registry_db),
    )["dimensions"]
    assert unmet_dims["guidance_locality"]["verdict"] == "unmet"


def test_restatement_detector_unlanded(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-restate",
        campaign_id="campaign-restate",
        restated=0,
    )
    dim = _group(
        score_selfassess_rubric("campaign-restate", db_path=registry_db),
    )["dimensions"]["restatement_discipline"]
    assert dim["verdict"] == "met"
    assert dim["evidence"]["detector_unlanded"] is True


def test_zero_fetch_vacuous_met(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-zero-fetch",
        campaign_id="campaign-zero-fetch",
        fetched=0,
        duplicate=0,
        restated=0,
    )
    dims = _group(
        score_selfassess_rubric("campaign-zero-fetch", db_path=registry_db),
    )["dimensions"]
    assert dims["duplicate_guidance"]["verdict"] == "met"
    assert dims["duplicate_guidance"]["evidence"]["vacuous"] is True
    assert dims["restatement_discipline"]["verdict"] == "met"
    assert dims["restatement_discipline"]["evidence"]["vacuous"] is True


def test_agent_judgment_dimensions(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-judgment",
        campaign_id="campaign-judgment",
        resident=15,
        fetched=25,
        artifact_count=4,
        trigger_fan_in=3,
    )
    dims = _group(
        score_selfassess_rubric("campaign-judgment", db_path=registry_db),
    )["dimensions"]
    suff = dims["guidance_sufficiency"]
    assert suff["verdict"] == "agent_judgment_required"
    assert suff["evidence"]["artifact_count"] == 4
    assert suff["evidence"]["resident_guidance_tokens"] == 15
    assert suff["evidence"]["trigger_fan_in_count_max"] == 3

    missed = dims["missed_guidance"]
    assert missed["verdict"] == "agent_judgment_required"
    assert missed["evidence"]["db_signal"] is None


def test_multi_row_group_sums_ratio_not_average(registry_db: Path) -> None:
    campaign_id = "campaign-multi-row"
    _insert_summary(
        registry_db,
        summary_id="summary-multi-a",
        campaign_id=campaign_id,
        fetched=100,
        duplicate=30,
    )
    _insert_summary(
        registry_db,
        summary_id="summary-multi-b",
        campaign_id=campaign_id,
        fetched=10,
        duplicate=0,
    )
    dim = _group(
        score_selfassess_rubric(campaign_id, db_path=registry_db),
    )["dimensions"]["duplicate_guidance"]
    assert dim["evidence"]["dup_ratio"] == pytest.approx(30 / 110)
    assert dim["verdict"] == "unmet"


def test_render_selfassess_closeout_non_empty_markdown(registry_db: Path) -> None:
    _insert_summary(
        registry_db,
        summary_id="summary-render",
        campaign_id="campaign-render",
    )
    report = score_selfassess_rubric("campaign-render", db_path=registry_db)
    markdown = render_selfassess_closeout(report)
    assert markdown.strip()
    for dimension in (
        "guidance_locality",
        "duplicate_guidance",
        "restatement_discipline",
        "delivery_auditability",
        "guidance_sufficiency",
        "missed_guidance",
    ):
        assert dimension in markdown
    assert "agent_judgment_required" in markdown or "met" in markdown
