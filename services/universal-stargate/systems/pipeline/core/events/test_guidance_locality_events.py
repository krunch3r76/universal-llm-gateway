"""Unit tests for token-locality guidance lifecycle event factories."""

from __future__ import annotations

from systems.pipeline.core.events.guidance_locality import (
    GuidanceDeliveryDeduped,
    GuidanceDeliveryRecorded,
    GuidanceRestatementDetected,
    GuidanceWorkflowSummarized,
)


def test_guidance_delivery_recorded_required_payload() -> None:
    event = GuidanceDeliveryRecorded(
        execution_id="exec-1",
        guidance_resource_key="agent-skills/foo.md",
        projection_surface="cursor_rules",
        delivered_tokens=42,
        fetch_scope="section",
        token_category="fetched_guidance",
    )
    assert event.signal == "guidance.delivery.recorded"
    assert event.role == "observation"
    assert event.scope == "global"
    assert event.payload == {
        "execution_id": "exec-1",
        "guidance_resource_key": "agent-skills/foo.md",
        "projection_surface": "cursor_rules",
        "delivered_tokens": 42,
        "fetch_scope": "section",
        "token_category": "fetched_guidance",
    }


def test_guidance_delivery_recorded_optional_fields() -> None:
    event = GuidanceDeliveryRecorded(
        execution_id="exec-1",
        guidance_resource_key="agent-skills/foo.md",
        projection_surface="cursor_rules",
        delivered_tokens=0,
        fetch_scope="section",
        token_category="resident_guidance",
        content_digest="abc123",
        delivered_bytes=0,
        is_duplicate=0,
        dedup_scope="turn",
        request_id="req-1",
        dispatch_id="disp-1",
        registry_schema_version="2",
        producer_version="v1",
    )
    assert event.payload["delivered_bytes"] == 0
    assert event.payload["is_duplicate"] == 0
    assert event.payload["content_digest"] == "abc123"
    assert event.payload["dedup_scope"] == "turn"
    assert event.payload["request_id"] == "req-1"


def test_guidance_delivery_recorded_omits_none_optionals() -> None:
    event = GuidanceDeliveryRecorded(
        execution_id="exec-1",
        guidance_resource_key="key",
        projection_surface="surface",
        delivered_tokens=10,
        fetch_scope="whole_doc",
        token_category="task_corpus",
        delivered_bytes=None,
        is_duplicate=None,
    )
    assert "delivered_bytes" not in event.payload
    assert "is_duplicate" not in event.payload


def test_guidance_delivery_deduped() -> None:
    event = GuidanceDeliveryDeduped(
        execution_id="exec-2",
        guidance_resource_key="shared-key",
        trigger_fan_in_count=3,
        dedup_scope="session",
    )
    assert event.signal == "guidance.delivery.deduped"
    assert event.role == "observation"
    assert event.scope == "global"
    assert event.payload == {
        "execution_id": "exec-2",
        "guidance_resource_key": "shared-key",
        "trigger_fan_in_count": 3,
        "dedup_scope": "session",
    }


def test_guidance_restatement_detected() -> None:
    event = GuidanceRestatementDetected(
        execution_id="exec-3",
        guidance_resource_key="agent-skills/bar.md",
        restated_overlap_tokens=15,
    )
    assert event.signal == "guidance.restatement.detected"
    assert event.role == "observation"
    assert event.scope == "global"
    assert event.payload["restated_overlap_tokens"] == 15


def test_guidance_workflow_summarized_token_vector() -> None:
    token_vector = {
        "resident_guidance_tokens": 100,
        "fetched_guidance_tokens": 50,
        "duplicate_guidance_tokens": 10,
        "tool_schema_tokens": 20,
        "task_corpus_tokens": 5,
        "transcript_restated_tokens": 7,
        "restated_overlap_tokens_total": 3,
        "whole_doc_bytes": 800,
        "section_bytes": 400,
        "trigger_fan_in_count": 2,
    }
    event = GuidanceWorkflowSummarized(
        execution_id="exec-4",
        workflow_class="implement",
        phase="closeout",
        token_vector=token_vector,
        campaign_id="camp-1",
        seat_substrate="cursor",
    )
    assert event.signal == "guidance.workflow.summarized"
    assert event.role == "observation"
    assert event.scope == "global"
    assert event.payload["token_vector"] == token_vector
    assert event.payload["token_vector"]["resident_guidance_tokens"] == 100
    assert event.payload["campaign_id"] == "camp-1"
    assert event.payload["seat_substrate"] == "cursor"
