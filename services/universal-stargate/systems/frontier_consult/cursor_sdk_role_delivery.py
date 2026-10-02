"""Bridge a cursor-sdk check/review closeout into a role-labeled agent-bus turn.

GIW success closeout (``routes/cursor_sdk.py``) calls
``should_bridge_cursor_check_review`` after the cursor-sdk reply posts.
Eligible contracts are historical ``none`` and job-vocab ``check-review``,
and only models on the cursor check/review allowlist. The role post fails
closed on a non-conforming closeout and does not replace the cursor-sdk reply.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx
from implement_admission.check_review_substrate import (
    cursor_delivery_from_role,
    is_cursor_check_review_model,
)
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .skeptic_evidence_grounding import parse_skeptic_reply_evidence

logger = get_logger(__name__)

_FILE_EVIDENCE_HEADER = re.compile(r"^FILE_EVIDENCE_PATHS:\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RoleDeliveryOutcome:
    posted: bool
    reason: str | None = None
    body_chars: int = 0


def _extract_findings_text(body: str) -> str:
    text = body.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict):
        return text
    for key in ("summary", "findings", "body", "content", "message"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return text


def _conforming_check_closeout(body: str) -> tuple[str, list[str]] | None:
    """Return (findings, file_paths) when closeout satisfies gate grammar."""
    findings = _extract_findings_text(body)
    if not findings.strip():
        return None
    paths, _mode, malformed = parse_skeptic_reply_evidence(findings)
    if malformed:
        return None
    if _FILE_EVIDENCE_HEADER.search(findings):
        if not paths:
            return None
    elif not paths:
        return None
    return findings, paths


def build_role_labeled_turn_body(findings: str, file_paths: list[str]) -> str:
    """Assemble the role-labeled turn body the check gate can parse.

    ``findings`` is the closeout prose. ``file_paths`` are appended under a
    ``FILE_EVIDENCE_PATHS:`` header, one path per line. Returns the combined
    text. Does not post or validate conformance.
    """
    lines = [findings.rstrip(), "", "FILE_EVIDENCE_PATHS:"]
    lines.extend(f"- {path}" for path in file_paths)
    return "\n".join(lines)


# ``none`` is the pre-split freeform token. Job vocabulary now stores
# ``check-review`` as its own handoff contract; ``sdk_contract_or_missing``
# returns that token unchanged. ``code-review`` and ``delivery-review`` stay
# off this bridge (empty ``delivery_role``; not this predicate).
_ROLE_BRIDGE_CONTRACTS = frozenset({"none", "check-review"})


def should_bridge_cursor_check_review(
    *,
    contract: str,
    resolved_model: str,
) -> bool:
    """Whether this closeout should post a second, role-labeled bus turn.

    ``contract`` is the stored handoff token (already lowercased by
    ``sdk_contract_or_missing`` on the GIW path). ``resolved_model`` must be
    in the cursor check/review allowlist. Returns False for every other job,
    including ``implement``, ``code-review``, and ``delivery-review``. No I/O.
    """
    token = (contract or "").strip().lower()
    return token in _ROLE_BRIDGE_CONTRACTS and is_cursor_check_review_model(
        resolved_model
    )


async def post_role_labeled_check_turn(
    *,
    thread_id: str,
    to_agent: str,
    delivery_from_role: str,
    closeout_body: str,
    subject: str | None = None,
) -> RoleDeliveryOutcome:
    """Post role-labeled ratifying turn; fail closed if closeout is empty."""
    parsed = _conforming_check_closeout(closeout_body)
    if parsed is None:
        return RoleDeliveryOutcome(posted=False, reason="non_conforming_closeout")

    findings, paths = parsed
    body = build_role_labeled_turn_body(findings, paths)
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        return RoleDeliveryOutcome(posted=False, reason="agent_bus_token_missing")

    payload = {
        "thread": thread_id,
        "from": delivery_from_role,
        "to": to_agent,
        "subject": subject or f"{delivery_from_role} check reply",
        "body": body,
        "status": "open",
        "allow_long_body": True,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.post("/turns", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("role delivery bridge transport error: %s", exc)
        return RoleDeliveryOutcome(posted=False, reason="transport_error")

    if resp.status_code >= 400:
        return RoleDeliveryOutcome(
            posted=False,
            reason=f"post_{resp.status_code}",
        )
    return RoleDeliveryOutcome(posted=True, body_chars=len(body))


def resolve_delivery_from_role(resolved_model: str) -> str | None:
    """Map an allowlisted cursor model to the bus author role for the bridge.

    ``resolved_model`` is the admit-time model id. Returns ``reviewer`` or
    ``skeptic`` when ``cursor_delivery_from_role`` recognizes the bare id,
    otherwise None so the closeout skips the role post. No I/O.
    """
    return cursor_delivery_from_role(resolved_model)
