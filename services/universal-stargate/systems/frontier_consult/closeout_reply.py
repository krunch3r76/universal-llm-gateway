"""Dispatched implement closeout reply — triggers pipeline:implement-closeout."""

from __future__ import annotations

import json
from typing import Any

from implement_admission.closeout_models import ImplementCloseout
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)


def parse_closeout_payload(body: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    text = body.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def run_implement_closeout_pipeline(
    closeout: dict[str, Any], *, source_ref: str | None = None
) -> dict[str, Any]:
    """Sync invoke pipeline:implement-closeout via Stargate chat completions."""
    model = ImplementCloseout.model_validate(closeout)
    if source_ref:
        model = model.model_copy(update={"source_ref": source_ref})
    options = {
        "closeout": model.model_dump(mode="json"),
        "source_ref": model.source_ref,
    }
    body = {
        "model": "implement-closeout",
        "messages": [{"role": "user", "content": "closeout"}],
        "pipeline_options": options,
    }
    with make_sync_client(DEFAULT_STARGATE_URL, timeout=120.0) as client:
        resp = client.post("/v1/chat/completions", json=body)
        if resp.status_code >= 400:
            return {"ok": False, "error": resp.text[:500]}
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return {"ok": False, "error": "empty pipeline response"}
        content = (choices[0].get("message") or {}).get("content") or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "non-json pipeline response",
                "raw": content[:500],
            }


def trigger_closeout_from_turn(
    *,
    thread_id: str,
    body: str | None,
    tags: list[str] | None,
) -> dict[str, Any] | None:
    """Run pipeline when thread is contract:implement and body is closeout JSON."""
    effective_tags = tags or []
    if "contract:implement" not in effective_tags:
        return None
    payload = parse_closeout_payload(body)
    if payload is None or payload.get("schema_version") != 1:
        return None
    if "source_ref" not in payload or "status" not in payload:
        return None
    logger.info("closeout_reply: triggering pipeline for thread %s", thread_id)
    return run_implement_closeout_pipeline(payload)
