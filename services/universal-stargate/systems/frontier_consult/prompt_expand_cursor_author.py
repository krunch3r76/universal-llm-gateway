"""Cursor-substrate author for prompt-expand TASK′.

The retrieve DAG returns an author bundle; this module renders
``prompts.yaml`` author_* templates and dispatches read-only
``cursor/grok-4.7`` via GIW — never chat completions or provider API ids.

Timeout note: prelude wall ``_EXPAND_TIMEOUT_S`` is 600s (retrieve ≤280 +
author). Author poll budget is 360s (former DAG author step timeout).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml
from universal_logging import get_logger

logger = get_logger(__name__)

AUTHOR_MODEL = "cursor/grok-4.7"
# Author generate uses contract=none so should_expand does not re-enter expand.
# Recurse-prevention only — not “do not expand unspecified operator tasks”.
AUTHOR_CONTRACT = "none"
# GIW's admit schema only accepts cursor-auto, stargate, or giw_park_resume.
# This call is the Stargate prelude, so the registered door is stargate.
AUTHOR_ADMITTED_VIA = "stargate"
AUTHOR_TIMEOUT_S = 360.0
_POLL_INTERVAL_S = 2.0
_TERMINAL = frozenset({"completed", "failed"})
_CDP_CODE_EXTRA_RE = re.compile(
    r"team_dispatch|manage|pipeline|panel_dispatch|quality_gate"
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "pipelines" / "prompt_expand" / "v1" / "prompts.yaml"
        if candidate.is_file():
            return parent
    return Path("/mnt/torus/projects/universal-llm-gateway")


def _prompts_path() -> Path:
    return _repo_root() / "pipelines" / "prompt_expand" / "v1" / "prompts.yaml"


def _worker_base_url() -> str:
    from systems.frontier_consult.cursor_sdk_worker_dispatch import worker_base_url

    return worker_base_url()


def load_author_prompt_specs() -> dict[str, Any]:
    """Load author_cdp / author_cursor entries from prompts.yaml."""
    raw = yaml.safe_load(_prompts_path().read_text(encoding="utf-8")) or {}
    prompts = raw.get("prompts") if isinstance(raw, dict) else None
    if not isinstance(prompts, dict):
        return {}
    return prompts


def render_author_message(bundle: dict[str, Any]) -> str:
    """Compose system + user author prompt for the Cursor card."""
    specs = load_author_prompt_specs()
    key = str(bundle.get("prompt_key") or "author_cursor")
    spec = specs.get(key) or specs.get("author_cursor") or {}
    system = str(spec.get("system_prompt") or "").strip()
    template = str(spec.get("template") or "")
    ctx = {
        "rag_context": bundle.get("rag_context") or "",
        "text": bundle.get("text") or "",
        "contract": bundle.get("contract") or "",
        "stage": bundle.get("stage") or "",
        "executor_tier": bundle.get("executor_tier") or "",
        "elicitation": "" if bundle.get("elicitation") is None else bundle.get("elicitation"),
    }
    try:
        user = template.format(**ctx)
    except KeyError as exc:
        raise ValueError(f"author template missing key {exc}") from exc
    if system:
        return f"{system}\n\n---\n\n{user}"
    return user


def cdp_forbidden_door(authored: str) -> str | None:
    """Return the first CODE_EXTRA door token when target is cdp."""
    match = _CDP_CODE_EXTRA_RE.search(authored or "")
    return match.group(0) if match else None


def parse_author_bundle(content: str) -> dict[str, Any] | None:
    """Parse pipeline completion JSON into an author bundle."""
    text = (content or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if "author_bundle" in data and isinstance(data["author_bundle"], dict):
        return data["author_bundle"]
    return data


async def _poll_via_http(
    *,
    dispatch_id: str,
    timeout_s: float,
    on_tick: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """HTTP facade of the ledger poll (Stargate process without GIW singleton)."""
    base = _worker_base_url()
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if on_tick is not None:
            on_tick()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{base}/api/v1/git/admin/dispatch-status",
                    params={"dispatch_id": dispatch_id},
                )
            if resp.status_code == 200:
                last = resp.json() if resp.content else {}
                if (last or {}).get("status") in _TERMINAL:
                    return {
                        "ok": True,
                        "terminal": True,
                        "status": last.get("status"),
                        "row": last,
                    }
        except (httpx.HTTPError, ValueError, OSError) as poll_exc:
            logger.warning("prompt-expand author status poll failed: %s", poll_exc)
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {
        "ok": False,
        "terminal": False,
        "reason": "dispatch_poll_timeout",
        "last": last,
        "dispatch_id": dispatch_id,
    }


async def _poll_author_terminal(
    *,
    thread_id: str,
    dispatch_id: str,
    timeout_s: float,
    on_tick: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Prefer in-process ``poll_dispatch_terminal_with_liveness``; else HTTP."""
    try:
        from services.git_integration_worker.cursor_auto.nested_sdk import (
            poll_dispatch_terminal_with_liveness,
        )
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        # Probe whether this process owns a live ledger singleton / DB.
        CursorDispatchLedger.instance()
    except Exception:  # noqa: BLE001 — Stargate often has no GIW ledger boot
        return await _poll_via_http(
            dispatch_id=dispatch_id, timeout_s=timeout_s, on_tick=on_tick
        )

    async def _tick(_row: dict[str, Any] | None) -> None:
        if on_tick is not None:
            on_tick()

    return await poll_dispatch_terminal_with_liveness(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        timeout_s=timeout_s,
        on_tick=_tick if on_tick is not None else None,
    )


async def author_task_prime_async(
    bundle: dict[str, Any],
    *,
    heartbeat_fn: Callable[[], None] | None = None,
    nest_under: str | None = None,
) -> str | None:
    """Dispatch read-only cursor/grok-4.7 and return TASK′ body, or None."""
    if not bundle.get("ok") or bundle.get("proceed") is False:
        return None
    try:
        message = render_author_message(bundle)
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("prompt-expand author render failed: %s", exc)
        return None

    dispatch_id = f"pe-author-{uuid.uuid4().hex[:12]}"
    execution_id = f"exec-{dispatch_id}"
    # Fresh thread + AUTHOR_CONTRACT=none: should_expand cannot re-admit expand.
    thread_id = f"pe-author-{uuid.uuid4().hex[:10]}"
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "model": AUTHOR_MODEL,
        "dispatch_id": dispatch_id,
        "execution_id": execution_id,
        "message": message,
        "handoff_contract": AUTHOR_CONTRACT,
        "read_only": True,
        "admitted_via": AUTHOR_ADMITTED_VIA,
        "close_contract": "auto",
    }
    if nest_under:
        payload["nest_under"] = nest_under
    # Intentionally omit lane — GIW rejects lane=B with read_only=true.

    base = _worker_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{base}/api/v1/cursor/dispatch", json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "prompt-expand author admit rejected status=%s body=%s",
                resp.status_code,
                (resp.text or "")[:200],
            )
            return None
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("prompt-expand author admit transport failed: %s", exc)
        return None

    if heartbeat_fn is not None:
        heartbeat_fn()

    polled = await _poll_author_terminal(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        timeout_s=AUTHOR_TIMEOUT_S,
        on_tick=heartbeat_fn,
    )
    if not polled.get("terminal"):
        logger.warning(
            "prompt-expand author poll not terminal dispatch_id=%s detail=%s",
            dispatch_id,
            polled.get("reason") or polled.get("status"),
        )
        return None
    if polled.get("status") == "failed":
        logger.warning(
            "prompt-expand author dispatch failed dispatch_id=%s", dispatch_id
        )
        return None

    from services.git_integration_worker.cursor_auto.nested_sdk import (
        fetch_sdk_closeout_body,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        full_result_text,
    )

    body = await fetch_sdk_closeout_body(
        thread_id=thread_id, dispatch_id=dispatch_id
    )
    text = full_result_text(body or "", None).strip()
    if not text:
        logger.warning(
            "prompt-expand author empty body dispatch_id=%s", dispatch_id
        )
        return None

    target = str(bundle.get("target") or "")
    if target == "cdp":
        forbidden = cdp_forbidden_door(text)
        if forbidden:
            logger.warning(
                "prompt-expand author cdp CODE_EXTRA door=%s", forbidden
            )
            return None
    return text


def author_task_prime(
    bundle: dict[str, Any],
    *,
    heartbeat_fn: Callable[[], None] | None = None,
    nest_under: str | None = None,
) -> str | None:
    """Sync entry for ``asyncio.to_thread`` / sync CDP doors (no running loop)."""
    return asyncio.run(
        author_task_prime_async(
            bundle, heartbeat_fn=heartbeat_fn, nest_under=nest_under
        )
    )


def author_from_pipeline_content(
    content: str,
    *,
    heartbeat_fn: Callable[[], None] | None = None,
    nest_under: str | None = None,
) -> str | None:
    """Parse pipeline completion JSON and author TASK′ on the Cursor substrate."""
    bundle = parse_author_bundle(content)
    if bundle is None:
        return None
    return author_task_prime(
        bundle, heartbeat_fn=heartbeat_fn, nest_under=nest_under
    )


__all__ = [
    "AUTHOR_CONTRACT",
    "AUTHOR_MODEL",
    "AUTHOR_TIMEOUT_S",
    "author_from_pipeline_content",
    "author_task_prime",
    "author_task_prime_async",
    "cdp_forbidden_door",
    "load_author_prompt_specs",
    "parse_author_bundle",
    "render_author_message",
]
