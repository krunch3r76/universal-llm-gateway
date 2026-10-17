"""Map pipeline exceptions to ``(code, message, data)`` tuples.

Extracted from ``core/executor.py`` so the proxy lifecycle layer and the
async-tracker tests have a stable, single-source import for the
``_normalize_pipeline_exception`` symbol. Behavior preserved byte-identical
from the pre-split implementation.

The leading underscore on ``_normalize_pipeline_exception`` is preserved
because existing consumers
(``proxy/stargate/requests/pipeline_lifecycle.py``,
``core/test_async_tracker_error_passthrough.py``) import it by that name.
"""

from __future__ import annotations

from typing import Any


def _first_dict_detail(exc: BaseException) -> dict[str, Any] | None:
    """Return the first dict-shaped ``detail`` on the exception chain."""
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
        if cause is not None:
            stack.append(cause)
        if context is not None and context is not cause:
            stack.append(context)
    return None


def _normalize_pipeline_exception(
    exc: BaseException,
) -> tuple[str, str, dict[str, Any] | None]:
    """Map known pipeline exceptions to ``(code, message, data)``.

    - ``code`` / ``message`` come from ``to_dict()`` when the exception
      provides one (e.g. ``PipelineError``), else from ``str(exc)``.
    - ``data`` carries the structured upstream body when the exception is
      a ``ProxyClientError`` with a dict-shaped ``detail`` — that path
      preserves provider HTTP 4xx/5xx JSON without flattening to a string.
    - Returns ``data=None`` for non-HTTP exceptions or when the upstream
      body was not JSON.
    """
    data: dict[str, Any] | None = _first_dict_detail(exc)

    # Step-level wrappers (e.g. ``PipelineExecutionError`` raised by the DAG
    # executor) lack ``to_dict`` but preserve the originating step exception
    # as ``__cause__``. Walk the cause chain so structured ``PipelineError``
    # subclasses (e.g. ``UnknownPipelineOptionsError``) surface their ``code``
    # to the final error envelope rather than collapsing to the generic
    # ``pipeline_execution_failed`` fallback.
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
            return code, message, data or payload
    return "pipeline_execution_failed", str(exc), data
