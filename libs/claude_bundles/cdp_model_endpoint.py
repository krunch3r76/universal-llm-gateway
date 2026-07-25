"""Shared CDP model-endpoint adapter (team_dispatch + pipeline fronts).

Thin wrapper over the native CDP contract (``cdp_ask.client.CdpAskClient`` /
Stargate ``POST /api/v1/providers/cdp/ask``). Terminal complete only after
harvest proof (``content_proof`` or ``archive_uri``) or failed+stall_stage.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from cdp_ask.client import CdpAskClient, CdpAskClientError, project_ask_base_url
from cdp_ask.models import SubmitProjectAskRequest

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

HarvestSource = Literal["chat", "output-file", "auto"]
ExpectedSize = Literal["small", "large", "auto"]


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
    return project_ask_base_url()


def picker_from_model_id(model_id: str) -> str:
    """Forward picker segment after ``cdp/`` unmodified (model-picker-is-SOT)."""
    if "/" not in model_id:
        return model_id
    provider, picker = model_id.split("/", 1)
    if provider != "cdp" or not picker:
        raise ValueError(f"expected cdp/<picker>, got {model_id!r}")
    return picker


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
    if status in {"failed", "aborted"}:
        return True
    # Transient transport errors while still pending/running keep polling.
    if snapshot.get("error") and status not in {"running", "pending"}:
        return True
    return False


def _completed_without_proof(snapshot: dict[str, Any]) -> bool:
    return str(snapshot.get("status") or "") == "completed" and not _has_proof(
        snapshot
    )


def _abort_then_sweep(
    satellite_id: str | None,
    execution_id: str,
    *,
    ask_client: CdpAskClient | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    abort_info: dict[str, Any] = {}
    if satellite_id:
        try:
            relay = ask_client or CdpAskClient()
            abort_info = relay.abort(satellite_id, client=client)
        except CdpAskClientError as exc:
            abort_info = _client_error_dict(exc)
    sweep_ephemeral(execution_id)
    return abort_info


def _client_error_dict(exc: CdpAskClientError) -> dict[str, Any]:
    out: dict[str, Any] = {"error": str(exc)}
    if exc.detail:
        out["detail"] = exc.detail
    if exc.status_code is not None:
        out["status_code"] = exc.status_code
    return out


def abort_cdp_generate(
    satellite_execution_id: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Abort in-flight satellite execution (separate failed path)."""
    try:
        return CdpAskClient().abort(satellite_execution_id, client=client)
    except CdpAskClientError as exc:
        return _client_error_dict(exc)


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
    harvest_source: HarvestSource = "auto",
    expected_size: ExpectedSize = "auto",
    download_output: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    client: httpx.Client | None = None,
    now: Callable[[], float] | None = None,
    ask_client: CdpAskClient | None = None,
) -> CdpGenerateResult:
    """Stage → native CDP submit → poll-to-proof (or stall/fail).

    Harvest/output knobs (``harvest_source``, ``expected_size``,
    ``download_output``) are forwarded on the native submit body — same fields
    as Stargate ``POST /api/v1/providers/cdp/ask``.
    """
    clock = now or time.monotonic
    picker = picker_from_model_id(model_id)
    relay = ask_client or CdpAskClient()
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

    submit_req = SubmitProjectAskRequest(
        prompt_uri=staged.prompt_uri,
        holder=holder,
        purpose=purpose,
        model=picker,
        converse=converse,
        no_project_uuid=no_project_uuid,
        harvest_source=harvest_source,
        expected_size=expected_size,
        download_output=download_output,
    )
    try:
        submitted = relay.submit(submit_req, client=client)
    except CdpAskClientError as exc:
        sweep_ephemeral(execution_id)
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=staged.prompt_uri,
            picker_model=picker,
            error=str(exc),
            extras=_client_error_dict(exc),
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
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
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
                extras={"abort": abort_info},
            )

        sleep(poll_interval_s)
        try:
            snapshot = relay.poll(sat_id, client=client)
        except CdpAskClientError as exc:
            snapshot = _client_error_dict(exc)
        polls += 1
        if snapshot.get("error") and "status" not in snapshot:
            if clock() - last_progress_at > no_progress_s:
                abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
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
                    extras={"abort": abort_info},
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

        if _completed_without_proof(snapshot):
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
            return CdpGenerateResult(
                ok=False,
                body=str(snapshot.get("body") or ""),
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="completed_without_proof",
                error="satellite completed without archive_uri or content_proof",
                poll_snapshots=polls,
                extras={"abort": abort_info},
            )

        if _terminal_failure(snapshot):
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
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
                extras={"abort": abort_info},
            )

        if clock() - last_progress_at > no_progress_s:
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
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
                extras={"abort": abort_info},
            )
