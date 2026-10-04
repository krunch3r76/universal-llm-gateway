"""Status basis envelope — wire contract for cross-boundary status reads.

Every monitor projection names ``source``, ``as_of``, ``scope``, and ``epoch``
alongside its primary value so callers can tell a hot read from a cold fold from
a liveness guess. Field names match ``busy_service_scope.project_service_busy``.
"""

from __future__ import annotations

from typing import Any

SOURCE_PIPELINE_TRACKER = "pipeline_tracker"
SOURCE_PIPELINE_DISPATCH_JOURNAL = "pipeline_dispatch_journal"
SOURCE_CDP_REGISTRY_EXECUTION_STATE = "cdp_registry.execution_state"
SOURCE_CDP_ASK_EXECUTION_STORE = "cdp_ask.execution_store"
SOURCE_THREAD_DISPATCH_LINKS = "thread_dispatch_links"

BASIS_KEYS = frozenset({"as_of", "source", "scope", "epoch"})


def status_basis(
    value_key: str,
    value: Any,
    *,
    as_of: str,
    source: str,
    scope: str,
    epoch: dict[str, Any],
    recovery: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Return a flat dict carrying *value* under *value_key* plus the basis tuple."""
    payload: dict[str, Any] = {
        value_key: value,
        "as_of": as_of,
        "source": source,
        "scope": scope,
        "epoch": epoch,
    }
    if recovery is not None:
        payload["recovery"] = recovery
    payload.update(extra)
    return payload
