"""Shared types for delivery-audit token-locality baseline harnesses."""

from __future__ import annotations

from dataclasses import dataclass

VALID_WORKFLOW_CLASSES = frozenset(
    {"simple_edit", "debugging", "review", "handoff_pickup", "session_close"}
)
VALID_PHASES = frozenset({"baseline", "post_change"})
VALID_SEAT_SUBSTRATES = frozenset({"cursor", "web-claude"})
SUMMARY_TOKEN_FIELDS = (
    "resident_guidance_tokens",
    "fetched_guidance_tokens",
    "duplicate_guidance_tokens",
    "tool_schema_tokens",
    "task_corpus_tokens",
    "transcript_restated_tokens",
)
INPUT_TOKEN_FIELDS = (
    "resident_guidance_tokens",
    "fetched_guidance_tokens",
    "tool_schema_tokens",
    "task_corpus_tokens",
    "transcript_restated_tokens",
)
P95_CAVEAT_THRESHOLD = 50


@dataclass(frozen=True)
class BaselineArtifact:
    """One delivered artifact in a baseline or post-change trace."""

    artifact_class: str
    artifact_id: str
    body: str | bytes = b""
    artifact_version: str | None = None
    artifact_revision: str | None = None
    artifact_title: str | None = None
    delivery_step: str = "baseline_trace"
    recipient_scope: str | None = None
    recipient_agent: str | None = None
    recipient_surface: str | None = None
    source_uri: str | None = None
    source_content_hash: str | None = None
    rendered_uri: str | None = None
    rendered_content_hash: str | None = None
    rendered_mime_type: str | None = "text/markdown"
    audit_status: str = "unaudited"
    audit_reason_code: str | None = None
    audit_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    dispatch_contract: str | None = None
    dispatch_role: str | None = None
    affordance_kind: str | None = None
    tool_surface: str | None = None
    guidance_resource_key: str | None = None
    projection_surface: str | None = None
    delivered_tokens: int | None = None
    fetch_scope: str | None = None
    token_category: str | None = None
    dedup_scope: str | None = "turn"
    whole_doc_reason: str | None = None
    restated_overlap_tokens: int | None = 0


@dataclass(frozen=True)
class BaselineTrace:
    """Tagged workflow trace consumed by the baseline collection harness."""

    workflow_class: str
    phase: str
    campaign_id: str
    seat_substrate: str
    artifacts: tuple[BaselineArtifact, ...]
    execution_id: str | None = None
    request_id: str | None = None
    dispatch_id: str | None = None
    pipeline_id: str | None = None
    dispatch_surface: str | None = None
    dispatch_contract: str | None = None
    dispatch_role: str | None = None
    dispatch_seat: str | None = None
    provider: str | None = None
    model: str | None = None
    dispatch_thread_id: str | None = None
    agent_bus_thread_id: str | None = None
    caller_agent: str | None = None
    transcript_id: str | None = None


def require_known(value: str, valid: frozenset[str], field: str) -> None:
    """Raise when a trace discriminator is outside its bound vocabulary."""
    if value not in valid:
        raise ValueError(f"unknown {field}: {value!r}")
