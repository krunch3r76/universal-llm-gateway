"""MCP thin relay for endeavor strategy-row writes (F-M2)."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ..db import WRITE_LOCK, cortex_conn
from ..endeavor_birth.write_row import dispose_row, write_row

logger = get_logger("cortex-api.dispatch_ops.endeavor_birth")


def _op_endeavor_write_row(
    host: str | None = None,
    fields: dict[str, Any] | None = None,
    **_: object,
) -> dict[str, Any]:
    if not host:
        return {"error": "host is required"}
    if not fields:
        return {"error": "fields is required"}
    try:
        return write_row(host, fields)
    except Exception as exc:  # noqa: BLE001
        logger.warning("endeavor_write_row failed for %s: %s", host, exc)
        if hasattr(exc, "status_code"):
            return {"error": str(getattr(exc, "detail", exc))}
        return {"error": str(exc)}


def _op_endeavor_dispose_row(
    host: str | None = None,
    row_id: str | None = None,
    disposition: str | None = None,
    reason: str | None = None,
    authority: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not host:
        return {"error": "host is required"}
    if not row_id:
        return {"error": "row_id is required"}
    if not disposition:
        return {"error": "disposition is required"}
    try:
        return dispose_row(host, row_id, disposition, reason, authority)
    except Exception as exc:  # noqa: BLE001
        logger.warning("endeavor_dispose_row failed for %s/%s: %s", host, row_id, exc)
        if hasattr(exc, "status_code"):
            return {"error": str(getattr(exc, "detail", exc))}
        return {"error": str(exc)}


def _op_endeavor_lock_ready(
    host: str | None = None,
    deliverable: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not host:
        return {"error": "host is required"}
    if not deliverable:
        return {"error": "deliverable is required"}
    from ..endeavor_birth.lock_model import lock_ready

    with cortex_conn() as conn:
        ready, blocking = lock_ready(conn, host, deliverable)
    return {"host": host, "deliverable": deliverable, "lock_ready": ready, "blocking_rows": blocking}


def _op_endeavor_repair_t1(**_: object) -> dict[str, Any]:
    from ..endeavor_birth.repair import apply_5129_repair

    with WRITE_LOCK, cortex_conn() as conn:
        return apply_5129_repair(conn)
