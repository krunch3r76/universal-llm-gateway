"""Global CDP → cursor-sdk reroute when ``cdp_reroute.enabled`` in route policy."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from claude_bundles.cdp_model_endpoint import CdpGenerateResult
from claude_bundles.nested_cdp_prompt_gate import resolve_cortex_uri
from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.routing import load_route_policy
from universal_logging import get_logger

from .cursor_sdk_generate import dispatch_cursor_sdk_generate
from .cursor_sdk_worker_dispatch import worker_base_url

logger = get_logger(__name__)

REROUTE_ADMIT_SUBSTRATE = "cursor-sdk-reroute"
_REROUTE_SUBSTRATE = REROUTE_ADMIT_SUBSTRATE
_POLL_INTERVAL_S = 3.0
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_STRUCTURED_CLOSEOUT_MARKER = "### Structured Closeout"
_REROUTE_HEADER = (
    "CDP reroute (cursor-sdk): read any referenced cortex:// artifacts with "
    'fs(op="read", path="cortex://...") and return the full review/bind '
    "response in your final message body (not acknowledgement-only).\n\n"
)


def load_cdp_reroute_config() -> dict[str, Any]:
    """Load ``cdp_reroute`` block from route policy (fresh read each call)."""
    policy = load_route_policy()
    block = policy.get("cdp_reroute")
    return block if isinstance(block, dict) else {}


def _parse_expires_at_utc(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_CRITIC_IMPRESARIO_ROLES = frozenset({"critic", "sketch", "skeptic", "review"})


def _impresario_critic_role_present(impresario_role: str | None) -> bool:
    if not impresario_role:
        return False
    role = impresario_role.strip().lower()
    return role in _CRITIC_IMPRESARIO_ROLES


def resolve_cdp_reroute(
    model_id: str,
    *,
    now: datetime | None = None,
    impresario_role: str | None = None,
) -> dict[str, Any] | None:
    """Return reroute target when policy matches ``model_id``, else ``None``."""
    if _impresario_critic_role_present(impresario_role):
        from implement_admission.impresario_policy import load_impresario_policy

        if not load_impresario_policy().cdp_reroute_for_critic:
            return None
    cfg = load_cdp_reroute_config()
    if cfg.get("enabled") is not True:
        return None
    expires = _parse_expires_at_utc(cfg.get("expires_at"))
    if expires is None:
        return None
    clock = (now or datetime.now(UTC)).astimezone(UTC)
    if clock >= expires:
        return None
    prefixes = cfg.get("match_prefixes") or ["cdp/opus", "cdp/fable"]
    model = (model_id or "").strip()
    if not model:
        return None
    for prefix in prefixes:
        if isinstance(prefix, str) and model.startswith(prefix):
            target_model = str(cfg.get("target_model") or "cursor/gemini-4-argon")
            if impresario_role:
                from implement_admission.impresario_events import (
                    ImpresarioCriticResolved,
                )

                from .cdp_events import publish_cdp_event

                publish_cdp_event(
                    ImpresarioCriticResolved(
                        role=impresario_role,
                        requested_model=model,
                        resolved_model=target_model,
                        transport="cursor_nested",
                        rerouted=True,
                    )
                )
            return {
                "target_model": target_model,
                "effort": str(cfg.get("effort") or "high"),
            }
    return None


def _strip_structured_closeout(text: str) -> str:
    idx = text.find(_STRUCTURED_CLOSEOUT_MARKER)
    if idx >= 0:
        return text[:idx].rstrip()
    return text


def _read_prompt_text(prompt_uri: str) -> str:
    path = resolve_cortex_uri(prompt_uri)
    return path.read_text(encoding="utf-8")


def _sidecar_path(dispatch_id: str) -> Path:
    from implement_admission.closeout_helpers import workspaces_root

    return workspaces_root() / "tmp/reviews/closeouts" / f"{dispatch_id}.md"


async def _poll_dispatch_status(
    *,
    dispatch_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    base = worker_base_url()
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{base}/api/v1/git/admin/dispatch-status",
                    params={"dispatch_id": dispatch_id},
                )
            if resp.status_code == 200:
                last = resp.json() if resp.content else {}
                status = str((last or {}).get("status") or "")
                if status in _TERMINAL:
                    return {
                        "ok": True,
                        "terminal": True,
                        "status": status,
                        "row": last,
                    }
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.warning(
                "cdp reroute poll failed dispatch_id=%s err=%s",
                dispatch_id,
                exc,
            )
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {
        "ok": False,
        "terminal": False,
        "reason": "dispatch_poll_timeout",
        "last": last,
        "dispatch_id": dispatch_id,
    }


async def _harvest_sdk_body(
    *,
    thread_id: str,
    dispatch_id: str,
) -> str:
    from services.git_integration_worker.cursor_sdk_deliverables import full_result_text
    from services.git_integration_worker.relay.dispatch_poll import (
        fetch_sdk_closeout_body,
    )

    sidecar = _sidecar_path(dispatch_id)
    if sidecar.is_file():
        raw = sidecar.read_text(encoding="utf-8")
    else:
        raw = (
            await fetch_sdk_closeout_body(
                thread_id=thread_id, dispatch_id=dispatch_id
            )
            or ""
        )
    text = full_result_text(raw, None).strip()
    return _strip_structured_closeout(text)


def _write_cdp_archive(*, execution_id: str, body: str) -> tuple[str, str]:
    short_id = execution_id[:8]
    rel = f"notes/system/threads/cdp-ask-archive-{short_id}.md"
    path = cortex_files_root() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"cortex://{rel}", digest


async def run_cdp_reroute_via_cursor_sdk(
    *,
    execution_id: str,
    prompt_uri: str,
    model_id: str,
    thread_id: str,
    caller_agent: str | None,
    max_wall_s: float | None = None,
    impresario_role: str | None = None,
) -> CdpGenerateResult:
    """Execute staged CDP prompt on cursor-sdk per global reroute policy."""
    reroute = resolve_cdp_reroute(model_id, impresario_role=impresario_role)
    if reroute is None:
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri,
            picker_model=model_id.split("/", 1)[-1],
            stall_stage="reroute_not_applicable",
            error="resolve_cdp_reroute returned None",
            substrate=_REROUTE_SUBSTRATE,
        )
    target_model = reroute["target_model"]
    effort = reroute["effort"]
    try:
        prompt_text = await asyncio.to_thread(_read_prompt_text, prompt_uri)
    except OSError as exc:
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri,
            picker_model=target_model.split("/", 1)[-1],
            stall_stage="reroute_prompt_read_failed",
            error=str(exc),
            substrate=_REROUTE_SUBSTRATE,
        )

    message = _REROUTE_HEADER + prompt_text
    request_id = f"cdp-reroute-{execution_id[:8]}-{uuid.uuid4().hex[:6]}"
    wall = float(max_wall_s) if max_wall_s is not None else float(
        os.environ.get("CDP_REROUTE_MAX_WALL_S", "3600")
    )

    try:
        admit = await dispatch_cursor_sdk_generate(
            request_id=request_id,
            role="cursor-sdk",
            model=target_model,
            subject=f"cdp reroute — {execution_id[:8]}",
            caller_agent=caller_agent or "stargate-cdp-reroute",
            contract="freeform",
            packet_path=None,
            message_text=message,
            bus_lifecycle="ephemeral",
            dispatch_thread_id=None,
            read_only=True,
            lane="B",
            model_knobs={"effort": effort},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cdp reroute admit failed execution_id=%s", execution_id)
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri,
            picker_model=target_model.split("/", 1)[-1],
            stall_stage="reroute_admit_failed",
            error=str(exc),
            substrate=_REROUTE_SUBSTRATE,
        )

    sdk_dispatch_id = str(admit.get("dispatch_id") or "")
    sdk_thread_id = str(admit.get("thread_id") or thread_id)
    if not sdk_dispatch_id:
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri,
            picker_model=target_model.split("/", 1)[-1],
            stall_stage="reroute_admit_missing_dispatch_id",
            error="dispatch_cursor_sdk_generate returned no dispatch_id",
            substrate=_REROUTE_SUBSTRATE,
        )

    polled = await _poll_dispatch_status(dispatch_id=sdk_dispatch_id, timeout_s=wall)
    status = str(polled.get("status") or "")
    if not polled.get("terminal") or status != "completed":
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=sdk_dispatch_id,
            prompt_uri=prompt_uri,
            picker_model=target_model.split("/", 1)[-1],
            stall_stage=f"reroute_poll_{status or polled.get('reason', 'timeout')}",
            error=str(polled.get("reason") or status or "poll failed"),
            substrate=_REROUTE_SUBSTRATE,
            cost_source="cursor-sdk",
        )

    body_text = await _harvest_sdk_body(
        thread_id=sdk_thread_id, dispatch_id=sdk_dispatch_id
    )
    if not body_text.strip():
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=sdk_dispatch_id,
            prompt_uri=prompt_uri,
            picker_model=target_model.split("/", 1)[-1],
            stall_stage="reroute_empty_harvest",
            error="empty closeout body",
            substrate=_REROUTE_SUBSTRATE,
            cost_source="cursor-sdk",
        )

    archive_uri, sha256_hex = _write_cdp_archive(
        execution_id=execution_id, body=body_text
    )
    return CdpGenerateResult(
        ok=True,
        body=body_text,
        execution_id=execution_id,
        satellite_execution_id=sdk_dispatch_id,
        prompt_uri=prompt_uri,
        picker_model=target_model.split("/", 1)[-1],
        archive_uri=archive_uri,
        content_proof_uri=archive_uri,
        content_proof_sha256=sha256_hex,
        substrate=_REROUTE_SUBSTRATE,
        cost_source="cursor-sdk",
    )


__all__ = [
    "REROUTE_ADMIT_SUBSTRATE",
    "load_cdp_reroute_config",
    "resolve_cdp_reroute",
    "run_cdp_reroute_via_cursor_sdk",
]
