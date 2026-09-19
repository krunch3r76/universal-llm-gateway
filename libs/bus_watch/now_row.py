"""Shared NOW row resolve+format — judgment ≻ policy ≻ friction ≻ summary ≻ attention."""

from __future__ import annotations

import re
from typing import Any, Literal

from bus_watch.friction_rows import now_row as friction_now_row
from bus_watch.judgment_rows import judgment_now_row
from bus_watch.spawn_pending import attention_now_row

Sources = Literal[
    "judgment", "policy", "friction", "summary", "attention", "empty", "tip"
]

_ENTITY_TOKEN_RE = re.compile(r"\b(?:todo|task|plan):[a-z0-9_-]+\b", re.I)
_TERMINAL_STATES = frozenset({"closed", "discharged", "terminal", "completed"})
_SUBJECT_CHARS = 56


def _policy_entity_tokens(bind: str) -> list[str]:
    return [m.group(0).lower() for m in _ENTITY_TOKEN_RE.finditer(bind)]


def policy_bind_satisfied(
    bind: str,
    entity_cache: dict[str, str] | None,
) -> bool:
    """R-a: keep policy bind unless every resolved entity token is terminal."""
    tokens = _policy_entity_tokens(bind)
    if not tokens:
        return True
    if not entity_cache:
        return True
    states = [entity_cache.get(tok) for tok in tokens]
    if any(s is None for s in states):
        return True
    if len(set(states)) > 1:
        return True
    return str(states[0]).lower() not in _TERMINAL_STATES


def build_entity_cache(
    policy_bind: str,
    *,
    entity_get: Any,
) -> dict[str, str]:
    """Cache ``{entity_id: workflow_state}`` for tokens in ``policy_bind``."""
    cache: dict[str, str] = {}
    for token in _policy_entity_tokens(policy_bind):
        entity_id = token if ":" in token else f"todo:{token}"
        try:
            payload = entity_get(entity_id)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        state = payload.get("workflow_state") or payload.get("status")
        if state is not None:
            cache[token] = str(state)
    return cache


def harvest_policy_entity_cache(policy_bind: str) -> dict[str, str]:
    """R-a entity cache during digest build (C-R2-5: friction_rows cortex client)."""
    from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

    def _entity_get(entity_id: str) -> dict[str, Any]:
        body = {
            "tool": "entity_get",
            "arguments": {"entity_id": entity_id, "intent": "card"},
        }
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=10.0) as cortex:
            r = cortex.post("/dispatch", json=body)
        if r.status_code >= 400:
            return {}
        payload = r.json()
        return payload if isinstance(payload, dict) else {}

    return build_entity_cache(policy_bind, entity_get=_entity_get)


def resolve_now_row(digest: dict[str, Any]) -> tuple[str, str]:
    """Return ``(raw_row, source)`` with full precedence stack."""
    judgment = judgment_now_row(digest)
    if judgment:
        return judgment
    policy = digest.get("policy") or {}
    policy_bind = str(policy.get("now_row") or "").strip()
    cache = digest.get("policy_entity_cache") or {}
    if policy_bind and policy_bind_satisfied(policy_bind, cache):
        return policy_bind, "policy"
    friction = friction_now_row(digest)
    if friction:
        return friction, "friction"
    summary = str(digest.get("summary_row") or "").strip()
    if summary:
        return summary, "summary"
    attention = attention_now_row(digest)
    if attention:
        return attention, "attention"
    return "", "empty"


def format_now_line(
    raw: str,
    source: str,
    digest: dict[str, Any],
    *,
    omit_tip_prefix: bool = False,
) -> str:
    """Format a NOW line for induction or spawn_wake successor context."""
    if source == "judgment":
        return str(raw or "").strip()
    if source in ("friction", "attention"):
        return str(raw or "").strip()
    root = digest.get("root") or {}
    root_id = root.get("id")
    tip = root.get("turns")
    if source == "summary":
        if tip is not None and root_id:
            handoff = str(root.get("last_subject") or "").strip()
            if handoff:
                base = (
                    f"tip turn #{tip} on agent-bus:{root_id} · "
                    f"«{handoff[:_SUBJECT_CHARS]}»"
                )
            else:
                base = f"tip turn #{tip} on agent-bus:{root_id}"
            return base if omit_tip_prefix else base
        return ""
    body = str(raw or "").strip()
    if len(body) > 120:
        body = body[:117].rstrip() + "…"
    if omit_tip_prefix or tip is None or not root_id:
        return body
    return f"tip turn #{tip} on agent-bus:{root_id} · {body}"


__all__ = [
    "Sources",
    "build_entity_cache",
    "format_now_line",
    "harvest_policy_entity_cache",
    "policy_bind_satisfied",
    "resolve_now_row",
]
