"""Stargate implement-closeout trigger helpers for cursor-sdk worker dispatches."""

from __future__ import annotations

import json

from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)


def build_closeout_idempotency_key(
    *, execution_id: str, thread_id: str, turn_number: int | None
) -> str:
    return f"implement-closeout:{execution_id}:{thread_id}:{turn_number}"


def build_closeout_trigger_payload(
    *, body_json: str, source_ref: str, idempotency_key: str
) -> dict:
    return {
        "closeout": json.loads(body_json),
        "source_ref": source_ref,
        "idempotency_key": idempotency_key,
    }


async def emit_implement_closeout_trigger(
    *, body_json: str, source_ref: str, idempotency_key: str
) -> None:
    """Fire-and-forget POST of the closeout to Stargate's /closeout ingress.

    Never raises: bus-delivery success is decoupled from trigger success.
    """
    payload = build_closeout_trigger_payload(
        body_json=body_json, source_ref=source_ref, idempotency_key=idempotency_key
    )
    try:
        async with make_async_client(DEFAULT_STARGATE_URL, timeout=10.0) as client:
            resp = await client.post("/api/v1/implement/closeout", json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "implement-closeout trigger rejected: status=%s key=%s body=%s",
                resp.status_code,
                idempotency_key,
                resp.text[:300],
            )
        else:
            logger.info("implement-closeout trigger accepted: key=%s", idempotency_key)
    except Exception as exc:  # never propagate
        logger.warning(
            "implement-closeout trigger transport error: key=%s err=%s",
            idempotency_key,
            exc,
        )


def extract_turn_number(body: object) -> int | None:
    if isinstance(body, dict):
        if isinstance(body.get("turn_number"), int):
            return body["turn_number"]
        turn = body.get("turn")
        if isinstance(turn, dict) and isinstance(turn.get("turn_number"), int):
            return turn["turn_number"]
    return None
