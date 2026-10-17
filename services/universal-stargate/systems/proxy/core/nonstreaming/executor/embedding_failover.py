"""CPU embedding failover helpers for execute_embedding_request."""

from __future__ import annotations

from fastapi import HTTPException
from universal_protocol import ErrorCode

CPU_EMBEDDING_MODEL_ID = "qwen3-embedding-8b-q8-0-8192-cpu"

_FAILOVER_TRIGGER_CODES = frozenset(
    {
        ErrorCode.NO_FEASIBLE_GATEWAY,
        ErrorCode.INSUFFICIENT_VRAM,
        ErrorCode.RESOURCE_UNAVAILABLE,
    }
)


def http_exception_triggers_cpu_failover(exc: HTTPException) -> bool:
    if exc.status_code != 503:
        return False
    detail = exc.detail
    if not isinstance(detail, dict):
        return False
    code = detail.get("code")
    if isinstance(code, ErrorCode):
        return code in _FAILOVER_TRIGGER_CODES
    return code in {c.value for c in _FAILOVER_TRIGGER_CODES}


def capacity_budget_expired(context) -> bool:
    import time

    deadline = getattr(context, "_capacity_deadline_mono", None)
    return deadline is not None and time.monotonic() >= deadline
