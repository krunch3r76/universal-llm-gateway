"""skill_suggest MCP tool — seat-routed relay to suggest or suggest-dispatch."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
from agent_seat.profiles import known_seats
from agent_seat.registry import normalize_agent_slug
from mcp_events import monotonic_now, record
from request_profile import current_request_metadata
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

from tools._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def _load_dispatch_enabled() -> bool:
    raw = os.environ.get("SKILL_SUGGEST_DISPATCH_ENABLED", "0")
    return raw.strip().lower() in {"1", "true", "on", "yes"}


_DISPATCH_ENABLED = _load_dispatch_enabled()
_DISPATCH_TIMEOUT = 330.0
_FALLBACK_WORKER_TIMEOUT_SECONDS = max(
    1,
    int(os.environ.get("SKILL_SUGGEST_WORKER_TIMEOUT_SECONDS", "20")),
)


def _should_relay_dispatch(agent: str) -> bool:
    if not _DISPATCH_ENABLED:
        return False
    return normalize_agent_slug(agent) in known_seats()


def _uses_dispatch_shim(agent: str, *, prefer_worker: bool | None) -> bool:
    """Legacy name — dispatch relay is seat-gated; worker hop is Stargate-side."""
    if not _should_relay_dispatch(agent):
        return False
    if prefer_worker is False:
        return False
    if prefer_worker is True:
        return True
    return True


def _is_cursor_origin(*, effective_agent: str, meta: dict[str, Any]) -> bool:
    profile = str(meta.get("request_profile") or meta.get("profile") or "").strip()
    if profile == "cursor_safe":
        return True
    if normalize_agent_slug(effective_agent) == "claude-cursor":
        return True
    caller = str(meta.get("caller_identity") or "").strip()
    if caller and normalize_agent_slug(caller) == "claude-cursor":
        return True
    return False


def _resolve_prefer_worker(
    explicit: bool | None,
    *,
    effective_agent: str,
) -> bool:
    if explicit is True:
        return True
    if explicit is False:
        return False
    meta = current_request_metadata()
    if _is_cursor_origin(effective_agent=effective_agent, meta=meta):
        return False
    return True


def _cap_worker_timeout_seconds(explicit: int | None) -> int:
    if explicit is None:
        return _FALLBACK_WORKER_TIMEOUT_SECONDS
    return min(explicit, _FALLBACK_WORKER_TIMEOUT_SECONDS)


def _route_reason_for_direct(*, agent: str, prefer_worker: bool | None) -> str:
    if not _DISPATCH_ENABLED:
        return "dispatch_disabled"
    if prefer_worker is False:
        return "prefer_worker_false"
    if normalize_agent_slug(agent) not in known_seats():
        return "unknown_seat"
    return "dispatch_not_selected"


def _annotate_direct_route(
    result: dict[str, Any],
    *,
    agent: str,
    prefer_worker: bool | None,
) -> dict[str, Any]:
    if "error" in result:
        return result
    annotated = dict(result)
    annotated["route"] = "direct"
    annotated["route_reason"] = _route_reason_for_direct(
        agent=agent,
        prefer_worker=prefer_worker,
    )
    return annotated


def _relay_suggest_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Thin relay to Stargate worker-hop endpoint (orchestration stays on Stargate)."""
    t0 = monotonic_now()
    record(
        "mcp.skill_suggest.dispatch_relay.called",
        agent=str(payload.get("agent") or ""),
        timeout_s=_DISPATCH_TIMEOUT,
    )
    try:
        with make_sync_client(
            DEFAULT_STARGATE_URL, timeout=_DISPATCH_TIMEOUT
        ) as client:
            response = client.post("/api/v1/skills/suggest-dispatch", json=payload)
    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        record(
            "mcp.skill_suggest.dispatch_relay.failed",
            error="request_error",
            duration_s=round(duration, 3),
            detail=str(exc),
        )
        logger.error("skill_suggest dispatch relay failed: %s", exc)
        return {
            "error": f"stargate connection failed: {exc}",
            "status_code": None,
        }

    duration = monotonic_now() - t0
    if response.status_code >= 400:
        detail = response.text
        record(
            "mcp.skill_suggest.dispatch_relay.failed",
            error="http_error",
            status_code=response.status_code,
            duration_s=round(duration, 3),
            **({"detail": detail[:500]} if detail else {}),
        )
        try:
            parsed_detail = response.json()
        except ValueError:
            parsed_detail = detail
        return {
            "error": f"stargate error: HTTP {response.status_code}",
            "status_code": response.status_code,
            "detail": parsed_detail,
        }

    try:
        result = response.json()
    except ValueError as exc:
        record(
            "mcp.skill_suggest.dispatch_relay.failed",
            error="non_json",
            status_code=response.status_code,
            duration_s=round(duration, 3),
            detail=str(exc),
        )
        return {
            "error": f"stargate returned non-JSON: HTTP {response.status_code}",
            "status_code": response.status_code,
        }

    record(
        "mcp.skill_suggest.dispatch_relay.ok",
        agent=str(payload.get("agent") or ""),
        route=str(result.get("route") or ""),
        duration_s=round(duration, 3),
    )
    return result


def _resolve_effective_agent(agent: str | None) -> str | None:
    if agent and str(agent).strip():
        canonical = normalize_agent_slug(str(agent).strip())
        if canonical not in known_seats():
            return None
        return canonical

    meta = current_request_metadata()
    caller_identity = str(meta.get("caller_identity") or "").strip()
    profile = str(meta.get("request_profile") or meta.get("profile") or "").strip()

    if caller_identity:
        canonical = normalize_agent_slug(caller_identity)
        if canonical in known_seats():
            return canonical

    if profile == "cursor_safe":
        return "claude-cursor"

    seat_class = str(meta.get("seat_class") or "").strip()
    if seat_class == "claude" and profile == "default":
        return "claude-web"

    return None


def register_skill_suggest_tools(mcp: FastMCP) -> None:
    """Register the skill_suggest thin relay tool."""

    @mcp.tool(title="Skill Suggest")
    def skill_suggest(
        loaded: list[str],
        conversation_context: str | None = None,
        limit: int | None = None,
        agent: str | None = None,
        prefer_worker: bool | None = None,
        worker_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Suggest newly relevant, not-yet-loaded skills for the caller seat.

        Returns ranked skill-slug deltas with a ``description`` per skill (from the
        skill index when available; synthesized by the ranker when missing). The
        server injects the caller seat when ``agent`` is omitted; pass ``agent``
        explicitly when seat resolution is unavailable.

        Each suggestion includes: ``id``, ``slug``, ``source_uri``, ``digest``,
        ``score``, ``description``, ``reason``, ``reason_source``. Tag lists
        (``trigger_match``) are internal scoring signals and are not returned.

        Response — two unreachable-skill channels. Check both for a complete view
        of skills that failed to load:

        ``degraded_skills`` (list) — skills omitted from ``suggestions`` entirely
        because source_uri is null, empty, or unparseable (slug could not be
        derived). Each entry: {id, name, skill_category, source_uri,
        degraded=true, reason="source_uri_null"|"source_uri_unparseable"}.
        These skills have no body/digest and are invisible to context scoring.

        ``suggestions[].digest == null`` — the skill appeared in ``suggestions``
        (source_uri was structurally parseable and slug derivable) but the
        referenced file could not be resolved on disk. The entry is visible and
        scored normally; only its body/digest is unavailable.

        These channels are mutually exclusive by design. Triage pattern:
            broken = degraded_skills + [s for s in suggestions if not s["digest"]]
        """
        effective_agent = _resolve_effective_agent(agent)
        if not effective_agent:
            if agent and str(agent).strip():
                return {
                    "error": (
                        f"unknown agent seat {normalize_agent_slug(str(agent).strip())!r}; "
                        "pass a known seat slug (e.g. claude-cursor, claude-web)"
                    )
                }
            return {
                "error": (
                    "agent seat could not be resolved from session context; "
                    "pass agent=<seat-slug> explicitly (e.g. claude-cursor, claude-web)"
                )
            }

        payload: dict[str, Any] = {
            "agent": effective_agent,
            "loaded": loaded,
        }
        if conversation_context is not None:
            payload["conversation_context"] = conversation_context
        if limit is not None:
            payload["limit"] = limit

        resolved_prefer_worker = _resolve_prefer_worker(
            prefer_worker,
            effective_agent=effective_agent,
        )
        if _should_relay_dispatch(effective_agent):
            payload["prefer_worker"] = resolved_prefer_worker
            payload["worker_timeout_seconds"] = _cap_worker_timeout_seconds(
                worker_timeout_seconds,
            )
            return _relay_suggest_dispatch(payload)

        return _annotate_direct_route(
            cx(
                "POST",
                "/skills/suggest",
                payload,
                headers={"X-Cortex-Transport": "mcp"},
            ),
            agent=effective_agent,
            prefer_worker=prefer_worker,
        )
