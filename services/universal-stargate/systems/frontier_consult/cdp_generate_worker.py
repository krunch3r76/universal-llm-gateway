"""Background worker + on-behalf delivery for CDP generate."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from claude_bundles.cdp_model_endpoint import (
    CDP_REPLY_FROM,
    CdpGenerateResult,
    run_cdp_generate,
)
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)


def _agent_bus_token() -> str:
    return os.getenv("AGENT_BUS_TOKEN", "").strip()


def format_cdp_result_body(result: CdpGenerateResult) -> str:
    """Render on-behalf turn body from adapter result."""
    if result.ok:
        lines = [
            f"# CDP generate result ({result.picker_model})",
            "",
            f"- execution_id: `{result.execution_id}`",
            f"- satellite_execution_id: `{result.satellite_execution_id}`",
            f"- substrate: `{result.substrate}`",
            f"- cost_source: `{result.cost_source}`",
        ]
        if result.archive_uri:
            lines.append(f"- archive_uri: `{result.archive_uri}`")
        if result.content_proof_uri:
            lines.append(f"- content_proof_uri: `{result.content_proof_uri}`")
        lines.extend(["", result.body or "_empty harvest_"])
        return "\n".join(lines)
    return "\n".join(
        [
            f"# CDP generate FAILED ({result.picker_model})",
            "",
            f"- execution_id: `{result.execution_id}`",
            f"- satellite_execution_id: `{result.satellite_execution_id}`",
            f"- stall_stage: `{result.stall_stage}`",
            f"- error: {result.error}",
            f"- substrate: `{result.substrate}`",
            f"- cost_source: `{result.cost_source}`",
        ]
    )


async def post_cdp_turn(
    *,
    thread_id: str,
    to_agent: str,
    subject: str,
    body: str,
    request_id: str,
) -> bool:
    """Post on-behalf bus turn as ``from=cdp``."""
    token = _agent_bus_token()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        logger.warning("cdp on-behalf post skipped: AGENT_BUS_TOKEN unset")
        return False
    payload: dict[str, Any] = {
        "thread": thread_id,
        "from": CDP_REPLY_FROM,
        "to": to_agent,
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": 0,
        "allow_long_body": True,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as client:
            resp = await client.post("/turns", json=payload, headers=headers)
            if resp.status_code >= 300:
                logger.warning(
                    "cdp on-behalf post failed: thread=%s status=%s body=%s",
                    thread_id,
                    resp.status_code,
                    resp.text[:300],
                )
                return False
            return True
    except Exception as exc:  # noqa: BLE001 — delivery best-effort
        logger.warning(
            "cdp on-behalf post transport error: thread=%s err=%s request_id=%s",
            thread_id,
            exc,
            request_id,
        )
        return False


async def run_cdp_worker(
    *,
    execution_id: str,
    model_id: str,
    thread_id: str,
    caller_agent: str | None,
    prompt_uri: str,
    request_id: str,
) -> None:
    """Stage already done at admit; run adapter and post proof/failure turn."""
    to_agent = caller_agent or "dispatch"
    try:
        result = await asyncio.to_thread(
            run_cdp_generate,
            execution_id=execution_id,
            model_id=model_id,
            prompt_uri=prompt_uri,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cdp worker crashed: execution_id=%s", execution_id)
        result = CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri,
            picker_model=model_id.split("/", 1)[-1],
            error=f"worker_crash: {exc}",
        )
    subject = (
        f"cdp reply — {execution_id[:8]}"
        if result.ok
        else f"cdp FAILED — {execution_id[:8]}"
    )
    await post_cdp_turn(
        thread_id=thread_id,
        to_agent=to_agent,
        subject=subject,
        body=format_cdp_result_body(result),
        request_id=request_id,
    )
