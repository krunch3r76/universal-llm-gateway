"""Thin Stargate client for on-behalf cortex thread sidecar writes.

Used by ``on_behalf.py`` during ``op="to_thread"`` async dispatch delivery: before
posting a dispatch result to an agent-bus thread, Stargate persists the full content as
a durable cortex sidecar via the ``thread_sidecar_write`` tool (through ``cx_async``).
The returned ``SidecarResult`` (URI, sha256, body length) lets oversized results be
replaced by a relocation pointer. Failures are logged and surfaced as ``None``, never
raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)

_PIPELINE_MODEL_SLUGS = frozenset({"team-dispatch", "chat-dispatch"})


def _model_from_entity_id(model_entity_id: str | None) -> str:
    if not model_entity_id:
        return ""
    slug = model_entity_id
    if slug.startswith("model:"):
        slug = slug[len("model:") :]
    if "claude-" in slug and not slug.startswith("anthropic/"):
        return f"anthropic/{slug}"
    return slug


def _effective_resolved_model(record: PipelineExecutionRecord) -> str:
    """Frontier model for economics; fallback when admission omitted resolved_model."""
    if record.resolved_model:
        return record.resolved_model
    result_model = record.result.model if record.result else ""
    if result_model and result_model not in _PIPELINE_MODEL_SLUGS:
        return result_model
    if record.result and record.result.model_entity_id:
        from_entity = _model_from_entity_id(record.result.model_entity_id)
        if from_entity:
            return from_entity
    return result_model


def _is_api_role_team_dispatch(record: PipelineExecutionRecord) -> bool:
    """True when API-role to_thread delivery should record economics."""
    if not record.work_key or not record.endpoint_request_id:
        return False
    if record.op != "to_thread" or record.result is None:
        return False
    from ...handlers.frontier_dispatch.cursor_sdk_dispatch import (
        is_cursor_sdk_dispatch_model,
    )

    return not is_cursor_sdk_dispatch_model(_effective_resolved_model(record))


def enrich_on_behalf_sidecar_content(
    record: PipelineExecutionRecord, content: str
) -> str:
    """Append token/cost footer before durable cortex sidecar write (API-role)."""
    if not _is_api_role_team_dispatch(record):
        return content
    from dispatch_economics.api_closeout import append_api_closeout_economics

    usage = record.result.usage if record.result is not None else None
    model = _effective_resolved_model(record)
    return append_api_closeout_economics(
        content,
        work_key=record.work_key,
        execution_id=record.execution_id,
        model=model,
        usage=usage,
        contract=record.resolved_contract,
    )


async def persist_api_role_ledger(record: PipelineExecutionRecord) -> None:
    """POST economics to git_integration_worker after successful on-behalf delivery."""
    if not _is_api_role_team_dispatch(record):
        return
    import httpx

    from systems.frontier_consult.cursor_sdk_worker_dispatch import worker_base_url

    usage = record.result.usage if record.result is not None else None
    model = _effective_resolved_model(record)
    if usage is not None:
        from dispatch_economics.api_closeout import normalize_api_role_usage

        usage = normalize_api_role_usage(usage, model=model)
    payload: dict[str, Any] = {
        "execution_id": record.execution_id,
        "work_key": record.work_key,
        "resolved_model": model,
        "contract": record.resolved_contract,
        "usage": usage,
    }
    url = f"{worker_base_url().rstrip('/')}/api/v1/cursor/api-role-dispatch"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 300:
            logger.warning(
                "api-role ledger relay failed: execution_id=%s status=%s body=%s",
                record.execution_id,
                resp.status_code,
                resp.text[:200],
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "api-role ledger relay unreachable: execution_id=%s err=%s",
            record.execution_id,
            exc,
        )


@dataclass
class SidecarResult:
    uri: str
    sha256: str
    body_chars: int


async def write_on_behalf_sidecar(
    record: PipelineExecutionRecord,
    *,
    content: str,
    thread: str,
    subject: str,
    oversized: bool,
) -> SidecarResult | None:
    """Persist a dispatch result as a cortex thread sidecar on behalf of the dispatched
    agent.

    Calls cortex tool ``thread_sidecar_write`` with the thread, subject, content, the
    record's ``from_agent`` (default ``"dispatch"``), ``execution_id`` and the
    ``oversized`` flag (content exceeds the bus body limit). Returns ``SidecarResult``
    on success; logs and returns ``None`` when cortex reports an error. ``on_behalf``
    treats a ``None`` result for oversized content as a terminal delivery failure.
    """
    from ...handlers.thread_persistence import cx_async

    result = await cx_async(
        "thread_sidecar_write",
        {
            "thread": thread,
            "subject": subject,
            "content": content,
            "from_agent": record.from_agent or "dispatch",
            "execution_id": record.execution_id,
            "oversized": oversized,
        },
    )
    if "error" in result:
        logger.error(
            "On-behalf sidecar write failed: execution_id=%s thread=%s error=%s",
            record.execution_id,
            thread,
            result.get("error"),
        )
        return None
    return SidecarResult(
        uri=result["uri"],
        sha256=result["sha256"],
        body_chars=result["body_chars"],
    )
