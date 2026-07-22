"""Shared CDP model-endpoint adapter (team_dispatch + pipeline fronts).

Wraps the Jupiter ``PROJECT_ASK_URL`` satellite (same transport as MCP
``project_ask``). Terminal complete only after harvest proof
(``content_proof`` or ``archive_uri``) or failed+stall_stage (D2 ceilings).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_prompt_uri,
    sweep_ephemeral,
)

DEFAULT_MAX_WALL_S = 1800
DEFAULT_NO_PROGRESS_S = 600
DEFAULT_POLL_INTERVAL_S = 2.0
CDP_SUBSTRATE = "web-anthropic-cdp"
CDP_REPLY_FROM = "cdp"


@dataclass(frozen=True, slots=True)
class CdpGenerateResult:
    """Outcome of one CDP generate run (adapter core)."""

    ok: bool
    body: str
    execution_id: str
    satellite_execution_id: str | None
    prompt_uri: str
    picker_model: str
    archive_uri: str | None = None
    content_proof_uri: str | None = None
    content_proof_sha256: str | None = None
    stall_stage: str | None = None
    error: str | None = None
    substrate: str = CDP_SUBSTRATE
    cost_source: str = "unavailable"
    poll_snapshots: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "body": self.body,
            "execution_id": self.execution_id,
            "satellite_execution_id": self.satellite_execution_id,
            "prompt_uri": self.prompt_uri,
            "picker_model": self.picker_model,
            "archive_uri": self.archive_uri,
            "content_proof_uri": self.content_proof_uri,
            "content_proof_sha256": self.content_proof_sha256,
            "stall_stage": self.stall_stage,
            "error": self.error,
            "substrate": self.substrate,
            "cost_source": self.cost_source,
            "poll_snapshots": self.poll_snapshots,
            **self.extras,
        }


def project_ask_url() -> str:
    """Return configured satellite base URL from ``PROJECT_ASK_URL`` env."""
    return os.environ.get("PROJECT_ASK_URL", "").strip()


def picker_from_model_id(model_id: str) -> str:
    """Forward picker segment after ``cdp/`` unmodified (model-picker-is-SOT)."""
    if "/" not in model_id:
        return model_id
    provider, picker = model_id.split("/", 1)
    if provider != "cdp" or not picker:
        raise ValueError(f"expected cdp/<picker>, got {model_id!r}")
    return picker


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    base = project_ask_url()
    if not base:
        return {
            "error": (
                "PROJECT_ASK_URL not configured. Start the cdp-ask satellite "
                "and set PROJECT_ASK_URL=http://HOST:PORT."
            )
        }
    url = f"{base.rstrip('/')}{path}"
    owns = client is None
    http = client or httpx.Client(timeout=timeout_s)
    try:
        resp = http.request(method, url, json=json_body)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return {"ok": True}
    except httpx.HTTPStatusError as exc:
        return {
            "error": f"project-ask HTTP {exc.response.status_code}",
            "detail": exc.response.text[:400],
        }
    except httpx.RequestError as exc:
        return {"error": f"project-ask unreachable: {exc}"}
    finally:
        if owns:
            http.close()


def _has_proof(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("archive_uri"):
        return True
    phase = str(snapshot.get("completion_phase") or "")
    if phase == "content_proof" and snapshot.get("content_proof_uri"):
        return True
    return False


def _progress_fingerprint(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    return (
        snapshot.get("completion_phase"),
        snapshot.get("body_len"),
        snapshot.get("status"),
        snapshot.get("streaming"),
        snapshot.get("tool_pause"),
        snapshot.get("liveness_observed_at"),
    )


def _terminal_failure(snapshot: dict[str, Any]) -> bool:
    status = str(snapshot.get("status") or "")
    if status in {"failed", "aborted", "cancelled"}:
        return True
    if snapshot.get("error") and status not in {"running", "queued"}:
        return True
    return False


def run_cdp_generate(
    *,
    execution_id: str,
    model_id: str,
    prompt_uri: str | None = None,
    prompt_text: str | None = None,
    packet_path: str | None = None,
    sidecar_ref: str | None = None,
    max_wall_s: float = DEFAULT_MAX_WALL_S,
    no_progress_s: float = DEFAULT_NO_PROGRESS_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    converse: bool = True,
    no_project_uuid: bool = True,
    purpose: str = "ask",
    holder: str = "cdp-model-endpoint",
    sleep: Callable[[float], None] = time.sleep,
    client: httpx.Client | None = None,
    now: Callable[[], float] | None = None,
) -> CdpGenerateResult:
    """Stage → submit → poll-to-proof (or stall/fail). Shared adapter core."""
    clock = now or time.monotonic
    picker = picker_from_model_id(model_id)
    try:
        staged = stage_prompt_uri(
            execution_id=execution_id,
            prompt_uri=prompt_uri,
            prompt_text=prompt_text,
            packet_path=packet_path,
            sidecar_ref=sidecar_ref,
        )
    except CdpStagingError as exc:
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri or "",
            picker_model=picker,
            error=exc.reason,
            stall_stage=None,
            extras={"code": exc.code},
        )

    submit_body = {
        "prompt_uri": staged.prompt_uri,
        "holder": holder,
        "purpose": purpose,
        "model": picker,
        "converse": converse,
        "no_project_uuid": no_project_uuid,
    }
    submitted = _relay(
        "POST",
        "/v1/project-ask/executions",
        json_body=submit_body,
        client=client,
    )
    if submitted.get("error"):
        sweep_ephemeral(execution_id)
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=staged.prompt_uri,
            picker_model=picker,
            error=str(submitted.get("error")),
            extras={"detail": submitted.get("detail")},
        )

    sat_id = str(submitted.get("execution_id") or "")
    if not sat_id:
        sweep_ephemeral(execution_id)
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=staged.prompt_uri,
            picker_model=picker,
            error="satellite submit returned no execution_id",
        )

    started = clock()
    last_fp = _progress_fingerprint(submitted)
    last_progress_at = started
    polls = 0

    while True:
        elapsed = clock() - started
        if elapsed > max_wall_s:
            sweep_ephemeral(execution_id)
            return CdpGenerateResult(
                ok=False,
                body="",
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="wall_clock_exceeded",
                error=f"CDP generate exceeded max_wall_s={max_wall_s}",
                poll_snapshots=polls,
            )

        sleep(poll_interval_s)
        snapshot = _relay(
            "GET",
            f"/v1/project-ask/executions/{sat_id}",
            client=client,
        )
        polls += 1
        if snapshot.get("error") and "status" not in snapshot:
            # Transport blip — count as no progress only; keep polling within ceilings.
            if clock() - last_progress_at > no_progress_s:
                sweep_ephemeral(execution_id)
                return CdpGenerateResult(
                    ok=False,
                    body="",
                    execution_id=execution_id,
                    satellite_execution_id=sat_id,
                    prompt_uri=staged.prompt_uri,
                    picker_model=picker,
                    stall_stage="no_progress",
                    error=str(snapshot.get("error")),
                    poll_snapshots=polls,
                )
            continue

        fp = _progress_fingerprint(snapshot)
        if fp != last_fp:
            last_fp = fp
            last_progress_at = clock()

        if _has_proof(snapshot):
            body = str(snapshot.get("body") or "")
            sweep_ephemeral(execution_id)
            return CdpGenerateResult(
                ok=True,
                body=body,
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                archive_uri=snapshot.get("archive_uri"),
                content_proof_uri=snapshot.get("content_proof_uri"),
                content_proof_sha256=snapshot.get("content_proof_sha256"),
                poll_snapshots=polls,
            )

        if _terminal_failure(snapshot):
            sweep_ephemeral(execution_id)
            return CdpGenerateResult(
                ok=False,
                body=str(snapshot.get("body") or ""),
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage=snapshot.get("stall_stage"),
                error=str(snapshot.get("error") or snapshot.get("status")),
                poll_snapshots=polls,
                extras={"abort": snapshot.get("aborted")},
            )

        if clock() - last_progress_at > no_progress_s:
            sweep_ephemeral(execution_id)
            return CdpGenerateResult(
                ok=False,
                body="",
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="no_progress",
                error=(
                    f"CDP generate no progress for no_progress_s={no_progress_s} "
                    f"(completion_phase={snapshot.get('completion_phase')!r})"
                ),
                poll_snapshots=polls,
            )


def abort_cdp_generate(satellite_execution_id: str) -> dict[str, Any]:
    """Abort in-flight satellite execution (separate failed path)."""
    return _relay(
        "POST",
        f"/v1/project-ask/executions/{satellite_execution_id}/abort",
    )
