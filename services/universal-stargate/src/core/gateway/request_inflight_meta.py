"""Per-request observability meta for in-flight tracking (admin active-work)."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_INFLIGHT_KINDS = frozenset(
    {"chat", "pipeline_sync", "generate_stream", "cdp_ask", "cloud", "unknown"}
)

_current_request_meta: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("stargate_inflight_request_meta", default=None)
)


def bind_request_inflight_meta(meta: dict[str, Any] | None) -> None:
    """Record meta for the current async task (captured at track/reserve time)."""
    if meta is None:
        _current_request_meta.set(None)
        return
    _current_request_meta.set(normalize_request_meta(meta))


@contextmanager
def inflight_meta_scope(meta: dict[str, Any] | None) -> Iterator[None]:
    """Bind meta for this scope and reset the contextvar token on exit."""
    if meta is None:
        token = _current_request_meta.set(None)
    else:
        token = _current_request_meta.set(normalize_request_meta(meta))
    try:
        yield
    finally:
        _current_request_meta.reset(token)


_CDP_ASK_ROUTE_PREFIXES = (
    "/api/v1/providers/cdp/ask",
    "/providers/cdp/ask",
)


def _path_is_cdp_ask_route(request_path: str) -> bool:
    path = (request_path or "").lower().split("?", 1)[0].rstrip("/")
    for prefix in _CDP_ASK_ROUTE_PREFIXES:
        normalized = prefix.lower().rstrip("/")
        if path == normalized or path.startswith(f"{normalized}/"):
            return True
    return False


def current_request_inflight_meta() -> dict[str, Any] | None:
    """Return bound meta for this task, if any."""
    return _current_request_meta.get()


def normalize_request_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Coerce meta to a stable shape; never raises."""
    try:
        kind_raw = meta.get("kind")
        kind = str(kind_raw).strip() if kind_raw is not None else "unknown"
        if kind not in _INFLIGHT_KINDS:
            kind = "unknown"
        out: dict[str, Any] = {"kind": kind}
        for key in ("caller_agent", "thread", "execution_id"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()
        return out
    except Exception as exc:  # noqa: BLE001 — observability must not break tracking
        logger.debug("normalize_request_meta failed: %s", exc)
        return {"kind": "unknown"}


def resolve_request_meta(explicit: dict[str, Any] | None) -> dict[str, Any]:
    """Prefer explicit meta; fall back to contextvar; default kind=unknown."""
    if explicit is not None:
        return normalize_request_meta(explicit)
    ctx = current_request_inflight_meta()
    if ctx is not None:
        return normalize_request_meta(ctx)
    return {"kind": "unknown"}


def _first_str(*candidates: Any) -> str | None:
    for val in candidates:
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def meta_from_http_request(
    request: Any,
    original_request: dict[str, Any] | None,
    *,
    kind: str,
) -> dict[str, Any]:
    """Build meta from FastAPI request headers and JSON body fields."""
    body = original_request if isinstance(original_request, dict) else {}
    headers = getattr(request, "headers", None)
    header_get = headers.get if headers is not None else lambda _k, _d=None: None

    thread = _first_str(
        body.get("dispatch_thread_id"),
        header_get("X-Dispatch-Thread-Id"),
        header_get("X-Dispatch-Thread-ID"),
    )
    caller_agent = _first_str(
        body.get("caller_agent"),
        header_get("X-Caller-Agent"),
        header_get("X-ULG-Caller-Agent"),
    )
    execution_id = _first_str(
        body.get("execution_id"),
        header_get("X-Pipeline-Execution-Id"),
        header_get("X-Execution-Id"),
    )
    return normalize_request_meta(
        {
            "kind": kind,
            "caller_agent": caller_agent,
            "thread": thread,
            "execution_id": execution_id,
        }
    )


def infer_inflight_kind(
    *,
    is_pipeline_internal: bool,
    is_pipeline_model: bool,
    client_wants_stream: bool,
    model_id: str,
    request_path: str = "",
) -> str:
    """Classify in-flight work for admin observability."""
    if _path_is_cdp_ask_route(request_path):
        return "cdp_ask"
    if is_pipeline_internal or is_pipeline_model:
        return "pipeline_sync"
    if client_wants_stream:
        return "generate_stream"
    model_lower = (model_id or "").lower()
    if model_lower.startswith(
        ("anthropic/", "openrouter/", "openai/", "google/", "x-ai/", "groq/")
    ):
        return "cloud"
    return "chat"
