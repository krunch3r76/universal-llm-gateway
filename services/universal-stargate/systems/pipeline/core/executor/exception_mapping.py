"""Map pipeline exceptions to ``(code, message, data)`` tuples.

Extracted from ``core/executor.py`` so the proxy lifecycle layer and the
async-tracker tests have a stable, single-source import for the
``_normalize_pipeline_exception`` symbol.

The leading underscore on ``_normalize_pipeline_exception`` is preserved
because existing consumers
(``proxy/stargate/requests/pipeline_lifecycle.py``,
``core/test_async_tracker_error_passthrough.py``) import it by that name.
"""

from __future__ import annotations

from typing import Any

_UPSTREAM_DATA_KEY = "upstream"


def _first_dict_detail(exc: BaseException) -> dict[str, Any] | None:
    """Return the first dict-shaped ``detail`` on the exception chain.

    Walks ``__cause__`` before ``__context__`` at each link.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        cid = id(current)
        if cid in seen:
            continue
        seen.add(cid)
        detail = getattr(current, "detail", None)
        if isinstance(detail, dict):
            return detail
        cause = current.__cause__
        context = current.__context__
        if context is not None and context is not cause:
            stack.append(context)
        if cause is not None:
            stack.append(cause)
    return None


def _wrap_upstream_data(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(detail, dict):
        return None
    return {_UPSTREAM_DATA_KEY: detail}


def _normalize_pipeline_exception(
    exc: BaseException,
) -> tuple[str, str, dict[str, Any] | None]:
    """Map known pipeline exceptions to ``(code, message, data)``.

    - ``code`` / ``message`` come from ``to_dict()`` when the exception
      provides one (e.g. ``PipelineError``), else from ``str(exc)``.
    - ``data`` carries structured upstream bodies under the ``upstream`` key
      when a dict-shaped ``detail`` was found on the exception chain.
    - Returns ``data=None`` for non-HTTP exceptions or when the upstream
      body was not JSON.
    """
    upstream_detail = _first_dict_detail(exc)

    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__

    for candidate in chain:
        if not hasattr(candidate, "to_dict"):
            continue
        try:
            payload = candidate.to_dict()
        except Exception:  # noqa: BLE001 — upstream exc shape varies
            payload = None
        if isinstance(payload, dict):
            code = str(
                payload.get("code")
                or payload.get("error_type")
                or "pipeline_execution_failed"
            )
            message = str(payload.get("message") or payload.get("error") or candidate)
            if upstream_detail is not None:
                return code, message, {**payload, _UPSTREAM_DATA_KEY: upstream_detail}
            return code, message, payload
    if upstream_detail is not None:
        return (
            "pipeline_execution_failed",
            str(exc),
            _wrap_upstream_data(upstream_detail),
        )
    return "pipeline_execution_failed", str(exc), None
