"""Envelope and subject composition for delivery turns.

Two flavours of body:

- **Legacy envelope** (``_build_envelope``): compact JSON pointer with
  ``execution_id``, ``pipeline``, ``status``, ``poll`` URL, optional
  ``usage`` / ``duration_s`` / ``hints`` / ``error.{code,message}`` /
  ``summary``. Full model output is never inlined — callers poll the
  execution endpoint for the complete result.

- **On-behalf reply** (handled in ``on_behalf.py``): ``record.result.content``
  posted verbatim. This module supplies the *subject* line for that path
  (``_build_on_behalf_subject``).

Subject helpers (``_build_subject``, ``_build_close_summary``,
``_build_on_behalf_subject``) live here so the legacy and bus-mode paths
share a single rendering surface.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord


def _build_envelope(
    record: PipelineExecutionRecord,
    brief_summary: str | None = None,
) -> str:
    """Render the legacy delivery body as a compact JSON pointer envelope.

    Full model output is never inlined — callers poll via the execution
    endpoint if they need the complete result.  Stays well under the
    agent-bus 8 000-char body limit for any realistic payload.
    """
    result = record.result
    error = record.error
    envelope: dict[str, Any] = {
        "execution_id": record.execution_id,
        "pipeline": record.pipeline,
        "status": record.status,
        "completed_at": record.completed_at,
        "poll": f"GET /api/v1/pipelines/executions/{record.execution_id}",
    }
    if result is not None:
        envelope["usage"] = result.usage
        envelope["duration_s"] = result.duration_s
        if result.hints:
            envelope["hints"] = result.hints
    if error is not None:
        envelope["error"] = {
            "code": error.code,
            "message": error.message,
        }
    if brief_summary is not None:
        envelope["summary"] = brief_summary
    return json.dumps(envelope, indent=2, default=str)


def _build_subject(record: PipelineExecutionRecord, override: str | None) -> str:
    """Prefer caller-supplied subject; fall back to pipeline + status."""
    if override:
        return override
    return f"async-dispatch {record.pipeline} {record.status}"


def _build_close_summary(
    record: PipelineExecutionRecord,
    *,
    prior_summary: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """Auto-generate a close summary from terminal record state.

    When *prior_summary* carries a standing so-what, preserve it via
    ``summary_for_auto_close`` (including ``LAND OWED —`` when branch debt
    or unlanded Lane-B meters apply). Otherwise fall back to a machine
    status one-liner from the execution record.
    """
    from agent_bus_store.disposition import (
        _closeout_land_meter_from_turn,
        summary_for_auto_close,
    )

    content = record.result.content if record.result is not None else ""
    landed, commits_ahead = _closeout_land_meter_from_turn(content)
    composed = summary_for_auto_close(
        prior_summary,
        tags=tags,
        landed=landed,
        commits_ahead=commits_ahead,
    )
    if composed is not None:
        return composed

    result = record.result
    error = record.error
    duration = result.duration_s if result is not None else None
    if error is not None:
        duration_str = f" after {duration:.1f}s" if duration is not None else ""
        return f"{record.status} ({error.code}){duration_str}"
    duration_str = f" in {duration:.1f}s" if duration is not None else ""
    return f"{record.status}{duration_str}"


def _build_on_behalf_subject(record: PipelineExecutionRecord) -> str:
    """Auto-derive the reply turn subject when none was caller-supplied."""
    if record.reply_subject:
        return record.reply_subject
    short_id = record.execution_id[:8]
    actor = record.from_agent or "dispatch"
    return f"{actor} reply — execution {short_id}"


def _extract_pointer_summary(content: str, *, max_chars: int = 300) -> str | None:
    body = content.lstrip()
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :].lstrip()
    if not body:
        return None
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    heading = None
    for ln in lines:
        if ln.startswith("#"):
            heading = ln.lstrip("#").strip()
            break
    prose = next(
        (ln for ln in lines if not ln.startswith(("#", "```", "|", "-", "*"))),
        "",
    )
    sentence = prose.split(". ")[0].strip()
    out = " — ".join(p for p in (heading, sentence) if p) or body
    return out[:max_chars].rstrip()


def _build_relocation_pointer(
    record: PipelineExecutionRecord,
    *,
    sidecar_uri: str,
    sha256: str,
    body_chars: int,
    summary: str | None,
) -> str:
    from .constants import _BUS_MAX_BODY_CHARS

    parts = [
        "**Full reply relocated to cortex (not lost).** "
        f"Body was {body_chars} chars (bus limit {_BUS_MAX_BODY_CHARS}).",
        "",
        f"- Durable copy: `{sidecar_uri}`",
        f"- sha256: `{sha256}`",
        f"- execution: `{record.execution_id}`",
    ]
    if summary:
        parts += ["", "**Summary:**", "", summary]
    parts += [
        "",
        f"_Read the full content: fs(cortex, read, {sidecar_uri.removeprefix('cortex://')})_",
    ]
    return "\n".join(parts)


def _build_inline_with_reference(content: str, *, sidecar_uri: str, sha256: str) -> str:
    return f"{content}\n\n---\n_Durable copy: `{sidecar_uri}` · sha256 `{sha256[:12]}`_"
