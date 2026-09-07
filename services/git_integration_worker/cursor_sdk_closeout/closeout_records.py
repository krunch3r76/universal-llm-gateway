"""Frozen value objects that cross the cursor-sdk closeout boundary.

Holds the three dataclasses produced or consumed by closeout assembly:
``SdkRunOutcome`` (live-run observation), ``PostWaitSnapshot`` (post-wait
conversation/artifact/git census), and ``CloseoutDelivery`` (assembled turn
body + sidecar receipt). This module is the package leaf: it imports nothing
from sibling closeout modules so every other module can depend on it without
cycles. Field comments on ``SdkRunOutcome`` are load-bearing provenance for
stream-only tool calls and local-bridge join keys — copy them with the class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import EffectsManifest
from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_manifest import CaptureBranch
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)


@dataclass(frozen=True)
class SdkRunOutcome:
    body: str
    status: str
    duration_ms: int
    tool_call_count: int
    effects_manifest: EffectsManifest | None = None
    capture_branch: CaptureBranch | None = None
    # Per-call detail from the live stream (friction 21654) — the channel that
    # can see a tool call the runtime truncates/rejects before it reaches
    # run.conversation(). Populated by observe_run_stream in the drive path.
    tool_calls: tuple[ToolCallObservation, ...] = ()
    # Normalized from TurnEndedUpdate.usage (+ TokenDelta fallthrough when absent).
    usage: dict[str, Any] | None = None
    usage_capture_status: str = "missing"
    sdk_request_id: str | None = None
    request_id_source: str | None = None
    # Local-bridge join keys when platform requestId is not on the wire (0.1.9:
    # request_id lives on SDKRequestMessage / CursorSDKError only — not RunResult).
    sdk_run_id: str | None = None
    sdk_agent_id: str | None = None
    degraded_reasons: tuple[str, ...] = ()
    sdk_git: dict[str, Any] | None = None
    stream_only_deviations: tuple[str, ...] = ()
    # Params actually passed to the bridge (ModelSelection.params at run start).
    model_knobs_emitted: dict[str, str] | None = None


@dataclass(frozen=True)
class PostWaitSnapshot:
    conversation: list[Any]
    artifact_paths: tuple[str, ...]
    sdk_git: dict[str, Any] | None


@dataclass(frozen=True)
class CloseoutDelivery:
    body: str
    sidecar_ref: str
    sidecar_path: Path
    full_result_bytes: int
    closeout_status: CloseoutStatus
