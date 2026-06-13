"""Tracker record dataclasses and their serialization.

``PipelineExecutionResult`` / ``PipelineExecutionError`` are the canonical
success / failure payloads; ``PipelineExecutionRecord`` is the per-execution row
the tracker retains, with ``to_dict`` producing the
``GET /api/v1/pipelines/executions/{id}`` response shape. All three are part of
the package public surface (re-exported from ``__init__``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True, kw_only=True)
class PipelineExecutionResult:
    """Canonical success payload captured by the tracker."""

    content: str
    model: str
    model_entity_id: str | None = None
    usage: dict[str, Any] | None = None
    duration_s: float = 0.0
    # Reasoning trace from reasoning-capable models (OpenAI GPT-5.x, o-series,
    # etc.). Shape preserved from upstream — structured blocks or a flat
    # string. ``None`` when the pipeline produced no reasoning trace.
    reasoning: Any = None
    # Structured anomaly/advisory hints from the dispatch step. Populated when
    # detectors fire (e.g. output_short on provider degradation) so polling
    # callers can triage silent failures without consulting the event service.
    # Each entry has at minimum ``type`` and ``reason`` keys.
    hints: list[dict[str, Any]] | None = None


@dataclass(slots=True, kw_only=True)
class PipelineExecutionError:
    """Canonical failure payload captured by the tracker.

    ``data`` holds the structured upstream error body when the failure
    originated from a provider HTTP 4xx/5xx with a JSON body
    (``ProxyClientError.detail`` when it arrived as a dict). Callers
    inspect ``data`` for provider-specific diagnostics (OpenAI's
    ``{type, code, param, message}`` shape, Anthropic's
    ``{type, error:{type, message}}``, etc.) without the adapter layer
    having to flatten them to strings. ``None`` when the failure was
    not an upstream HTTP error or the body was not JSON.
    """

    code: str
    message: str
    data: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class DeliveryState:
    """Caller-facing delivery outcome for op="to_thread" records (friction 16985)."""

    status: Literal["delivered", "failed", "skipped"]
    mode: Literal["inline", "sidecar"] | None = None
    thread: str | None = None
    sidecar_uri: str | None = None
    content_sha256: str | None = None
    failure_reason: str | None = None

    def to_dict(self, execution_id: str) -> dict[str, Any]:
        if self.sidecar_uri:
            kind = "sidecar"
        elif self.status == "delivered":
            kind = "thread"
        else:
            kind = "pipeline_result"

        if kind == "sidecar":
            path = self.sidecar_uri.removeprefix("cortex://")
            hint = f"Read the durable copy: fs(cortex, op=read, path={path})."
            if self.status == "failed":
                hint += (
                    " Bus delivery failed, but the full content is persisted "
                    "in the sidecar above."
                )
        elif kind == "thread":
            hint = f"Delivered to agent-bus thread {self.thread}."
        else:
            hint = (
                "Delivery failed; retrieve the result via "
                f"pipeline(op=result, execution_id={execution_id}) "
                "before tracker retention expires."
            )

        return {
            "attempted": True,
            "status": self.status,
            "mode": self.mode,
            "thread": self.thread,
            "sidecar_uri": self.sidecar_uri,
            "content_sha256": self.content_sha256,
            "failure_reason": self.failure_reason,
            "recovery": {
                "kind": kind,
                "thread": self.thread,
                "execution_id": execution_id,
                "sidecar_uri": self.sidecar_uri,
                "hint": hint,
            },
        }


@dataclass(slots=True, kw_only=True)
class PipelineExecutionRecord:
    """Per-execution record retained by the tracker.

    Uses ``field(default_factory=...)`` for timestamp and event defaults so
    every record gets a fresh value (bare ``datetime.now`` defaults would
    evaluate once at class-definition time).
    """

    execution_id: str
    pipeline: str
    status: str  # "running" | "completed" | "failed"
    started_at: str
    started_at_monotonic: float
    completed_at: str | None = None
    completed_at_monotonic: float | None = None
    result: PipelineExecutionResult | None = None
    error: PipelineExecutionError | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    terminal_event: asyncio.Event = field(default_factory=asyncio.Event)
    # dispatch-surface-split Phase 1: output contract + op tracking
    output_contract: Literal["inline", "thread"] = "inline"
    # Mirrors result_delivery.bus_thread for op="to_thread"
    target_thread: str | None = None
    # None = no op discrimination supplied (direct pipeline callers)
    op: Literal["generate", "to_thread"] | None = None
    # ISO-8601 Z; populated when the system-on-behalf post lands on
    # target_thread. Field name preserved for tracker.to_dict back-compat;
    # semantics shifted from "observed reply" to "post completed" in the
    # to-thread delivery architectural fix (2026-05-22) — see
    # notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md.
    thread_reply_observed_at: str | None = None
    # Identity to post as for op="to_thread". Populated at admission from the
    # role (team_dispatch) or model identifier (persona-free frontier HTTP). Reply
    # turns are posted from this agent to record.caller_agent (or a thread
    # fallback) by the delivery handler.
    from_agent: str | None = None
    # Caller-supplied subject for the on-behalf reply turn. None ⇒ auto-derive.
    reply_subject: str | None = None
    # Post-delivery thread disposition for ``op="to_thread"``. ``ephemeral``
    # closes the bus thread after a successful on-behalf POST (team-dispatch
    # one-shots default ephemeral at admission).
    bus_lifecycle: Literal["persistent", "ephemeral"] = "ephemeral"
    # Caller-facing delivery outcome (friction 16985). None until op="to_thread"
    # delivery runs; legacy result_delivery path leaves this None.
    delivery: DeliveryState | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the shape returned by ``GET /api/v1/pipelines/executions/{id}``."""  # noqa: E501
        result_payload: dict[str, Any] | None = None
        if self.result is not None:
            result_payload = {
                "content": self.result.content,
                "model": self.result.model,
                "model_entity_id": self.result.model_entity_id,
                "usage": self.result.usage,
                "duration_s": self.result.duration_s,
                "reasoning": self.result.reasoning,
                "hints": self.result.hints or [],
            }
        error_payload: dict[str, Any] | None = None
        if self.error is not None:
            error_payload = {
                "code": self.error.code,
                "message": self.error.message,
                # Structured upstream body when the failure was an HTTP
                # error with a JSON response. Callers treat absence as
                # "no structured data available", not as an error.
                "data": self.error.data,
            }
        return {
            "execution_id": self.execution_id,
            "pipeline": self.pipeline,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": result_payload,
            "error": error_payload,
            "caller_agent": self.caller_agent,
            "output_contract": self.output_contract,
            "target_thread": self.target_thread,
            "op": self.op,
            "thread_reply_observed_at": self.thread_reply_observed_at,
            "delivery": (
                self.delivery.to_dict(self.execution_id)
                if self.delivery is not None
                else None
            ),
        }
