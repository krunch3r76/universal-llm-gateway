"""Stargate implement-closeout trigger helpers for cursor-sdk worker dispatches.

Normalizes sidecar ``workspaces://`` refs to sandbox-relative ``packet:`` form
before POSTing implement-closeout payloads to Stargate ingress.
"""

from __future__ import annotations

import json
import re

from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_ULG_REPO = "universal-llm-gateway"
_WORKSPACES_URI_RE = re.compile(
    rf"^workspaces://{_ULG_REPO}/(?P<rel>.+)$",
    re.IGNORECASE,
)
_WORKSPACES_REPO_URI_RE = re.compile(
    r"^workspaces://(?P<repo>[^/]+)/(?P<rel>.+)$",
    re.IGNORECASE,
)
_UNCHANGED_PREFIXES = ("todo:", "packet:", "agent-bus:", "plan:", "plan_phase:", "task:")


def normalize_closeout_source_ref(ref: str) -> str:
    """Normalize sidecar/work-item refs for Stargate ``PacketCloseoutAdapter``.

    Bare ``workspaces://universal-llm-gateway/<rel>`` maps to sandbox-relative
    ``packet:<rel>`` so ``parse_source_ref`` and ``PacketCloseoutAdapter`` resolve
    the on-disk sidecar under the workspaces root. ``todo:``, existing ``packet:``,
    and ``agent-bus:`` refs pass through unchanged.
    """
    text = (ref or "").strip()
    if not text:
        return text
    lower = text.lower()
    if lower.startswith(_UNCHANGED_PREFIXES):
        return text
    match = _WORKSPACES_URI_RE.match(text)
    if match is not None:
        return f"packet:{match.group('rel').lstrip('/')}"
    repo_match = _WORKSPACES_REPO_URI_RE.match(text)
    if repo_match is not None:
        repo = repo_match.group("repo")
        rel = repo_match.group("rel").lstrip("/")
        return f"packet:{repo}/{rel}"
    return text


def build_closeout_idempotency_key(
    *, execution_id: str, thread_id: str, turn_number: int | None
) -> str:
    """Build the Stargate dedupe key for one implement-closeout trigger POST."""
    return f"implement-closeout:{execution_id}:{thread_id}:{turn_number}"


def build_closeout_trigger_payload(
    *, body_json: str, source_ref: str, idempotency_key: str
) -> dict:
    """Assemble the JSON body for ``POST /api/v1/implement/closeout``."""
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
    normalized_ref = normalize_closeout_source_ref(source_ref)
    payload = build_closeout_trigger_payload(
        body_json=body_json, source_ref=normalized_ref, idempotency_key=idempotency_key
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
    """Extract agent-bus turn number from a cursor-sdk closeout reply body."""
    if isinstance(body, dict):
        if isinstance(body.get("turn_number"), int):
            return body["turn_number"]
        turn = body.get("turn")
        if isinstance(turn, dict) and isinstance(turn.get("turn_number"), int):
            return turn["turn_number"]
    return None
