"""Bounded synchronous Stage-B rerank client for skill_suggest (§4)."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger("cortex-api.skill_suggest_rank")

STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_PIPELINE_ID = "skill-suggest-rank"
_TIMEOUT_S = float(os.environ.get("SKILL_SUGGEST_RERANK_TIMEOUT_S", "2.5"))
_MAX_INFLIGHT = int(os.environ.get("SKILL_SUGGEST_RERANK_MAX_INFLIGHT", "4"))
_CIRCUIT_FAILURE_THRESHOLD = int(
    os.environ.get("SKILL_SUGGEST_RERANK_CIRCUIT_FAILURES", "5")
)
_CIRCUIT_COOLDOWN_S = float(os.environ.get("SKILL_SUGGEST_RERANK_CIRCUIT_COOLDOWN_S", "30"))

_semaphore = threading.BoundedSemaphore(_MAX_INFLIGHT)
_circuit_lock = threading.Lock()
_consecutive_failures = 0
_circuit_open_until = 0.0


def rerank_enabled_default() -> bool:
    return os.environ.get("SKILL_SUGGEST_RERANK_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _circuit_open() -> bool:
    return time.monotonic() < _circuit_open_until


def _record_failure() -> None:
    global _consecutive_failures, _circuit_open_until
    with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_S
            _consecutive_failures = 0


def _record_success() -> None:
    global _consecutive_failures, _circuit_open_until
    with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open_until = 0.0


def _candidate_payload(stage_a_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "slug": item["slug"],
            "trigger_short": item.get("trigger_short", ""),
            "trigger_match_terms": item.get("trigger_match_terms", []),
            "skill_category": item.get("skill_category", ""),
        }
        for item in stage_a_candidates
    ]


def _parse_ranked(
    raw: str,
    *,
    allowed_slugs: set[str],
    loaded_set: set[str],
) -> list[dict[str, str]] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    ranked = parsed.get("ranked")
    if not isinstance(ranked, list) or not ranked:
        return None
    out: list[dict[str, str]] = []
    for item in ranked:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        reason = item.get("reason")
        if not isinstance(slug, str) or not isinstance(reason, str):
            continue
        slug_norm = slug.strip()
        if slug_norm not in allowed_slugs or slug_norm in loaded_set:
            continue
        out.append({"slug": slug_norm, "reason": reason[:120]})
    return out or None


def apply_rerank(
    *,
    stage_a_result: dict[str, Any],
    stage_a_candidates: list[dict[str, Any]],
    conversation_context: str,
    loaded: list[str],
    limit: int,
) -> tuple[dict[str, Any], str, str | None, str | None]:
    """Return (updated_result, ranker_status, degraded_reason, rank_execution_id)."""
    from .routes._skill_suggest import build_loaded_set

    if _circuit_open():
        result = dict(stage_a_result)
        result["degraded"] = True
        return result, "error", "circuit_open", None

    if not _semaphore.acquire(blocking=False):
        result = dict(stage_a_result)
        result["degraded"] = True
        return result, "error", "concurrency_cap", None

    allowed = {item["slug"] for item in stage_a_candidates}
    loaded_set = build_loaded_set(loaded)
    payload = {
        "model": _PIPELINE_ID,
        "messages": [{"role": "user", "content": "rank"}],
        "pipeline_options": {
            "candidates": _candidate_payload(stage_a_candidates),
            "context": conversation_context,
            "loaded": sorted(loaded_set),
            "limit": limit,
        },
    }
    rank_execution_id: str | None = None
    try:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.post(f"{STARGATE_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            body = resp.json()
            rank_execution_id = body.get("execution_id") or body.get("id")
            content = ""
            choices = body.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = str(message.get("content") or "")
            ranked = _parse_ranked(
                content, allowed_slugs=allowed, loaded_set=loaded_set
            )
            if ranked is None:
                _record_failure()
                result = dict(stage_a_result)
                result["degraded"] = True
                return result, "invalid_output", "invalid_output", rank_execution_id
    except httpx.TimeoutException:
        _record_failure()
        result = dict(stage_a_result)
        result["degraded"] = True
        return result, "timeout", "timeout", rank_execution_id
    except Exception:
        logger.warning("skill_suggest rerank failed", exc_info=True)
        _record_failure()
        result = dict(stage_a_result)
        result["degraded"] = True
        return result, "error", "error", rank_execution_id
    finally:
        _semaphore.release()

    _record_success()
    by_slug = {item["slug"]: item for item in stage_a_result.get("suggestions", [])}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        slug = item["slug"]
        base = by_slug.get(slug)
        if base is None:
            continue
        updated = dict(base)
        updated["reason"] = item["reason"]
        updated["reason_source"] = "model"
        ordered.append(updated)
        seen.add(slug)
    for base in stage_a_result.get("suggestions", []):
        if base["slug"] not in seen:
            ordered.append(base)
    ordered = ordered[:limit]
    result = dict(stage_a_result)
    result["suggestions"] = ordered
    result["count"] = len(ordered)
    result["ranker_status"] = "ok"
    result["degraded"] = False
    return result, "ok", None, rank_execution_id


def reset_circuit_for_tests() -> None:
    """Test helper — reset circuit-breaker state."""
    global _consecutive_failures, _circuit_open_until
    with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open_until = 0.0


def norm_loaded(value: str) -> str:
    from .routes._skill_suggest import norm_loaded as _norm

    return _norm(value)
