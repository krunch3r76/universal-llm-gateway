"""Copy outstanding CDP generate fire rows onto a resume_of child ledger.

Friction 37401: ``park_for_restart`` (and hand ``resume_of``) mint a new bridge
keyed by the child dispatch id. The child's ``*.cdp-generates.jsonl`` starts
empty, so the observer cannot mark a parent-fired generate received and
closeout does not park ``await_cdp_reply``. Copy the parent's still-outstanding
fire rows at admit.

Skip ``giw_await_reply_resume`` — those ids are already delivered in the
CDP-REPLY-RESUME preamble; copying them would re-park the child in a loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_logging import get_logger

from scripts.mcp_bridge_generate_ledger import generate_ledger_path
from services.git_integration_worker.cursor_sdk_await_reply import (
    ADMITTED_VIA_AWAIT_RESUME,
    fired_generates,
    outstanding_generates,
    received_execution_ids,
)
from services.git_integration_worker.cursor_sdk_context import steer_spool_dir

logger = get_logger(__name__)


def inherit_outstanding_generates(
    *,
    parent_id: str,
    child_id: str,
    admitted_via: str | None = None,
    spool_dir: Path | str | None = None,
) -> list[str]:
    """Append parent's outstanding fire rows onto the child's JSONL.

    Fail-open: never raise into admit. Idempotent: skips execution ids already
    present as fire or received rows on the child. Returns copied ids.
    """
    try:
        return _inherit(
            parent_id=parent_id,
            child_id=child_id,
            admitted_via=admitted_via,
            spool_dir=spool_dir,
        )
    except Exception:  # noqa: BLE001 — never block resume_of admit
        logger.exception(
            "generate-ledger inherit failed open parent=%s child=%s",
            parent_id,
            child_id,
        )
        return []


def _inherit(
    *,
    parent_id: str,
    child_id: str,
    admitted_via: str | None,
    spool_dir: Path | str | None,
) -> list[str]:
    parent = parent_id.strip()
    child = child_id.strip()
    if not parent or not child or parent == child:
        return []
    if admitted_via == ADMITTED_VIA_AWAIT_RESUME:
        return []
    root = Path(spool_dir) if spool_dir is not None else steer_spool_dir()
    wanted = {
        g.execution_id for g in outstanding_generates(parent, spool_dir=root)
    }
    if not wanted:
        return []
    already = {g.execution_id for g in fired_generates(child, spool_dir=root)}
    already |= received_execution_ids(child, spool_dir=root)
    wanted -= already
    if not wanted:
        return []
    parent_path = generate_ledger_path(root, parent)
    if not parent_path.is_file():
        return []
    copied: list[str] = []
    seen: set[str] = set()
    lines: list[str] = []
    for raw in parent_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("kind") or "") == "received":
            continue
        execution_id = parsed.get("execution_id")
        if not isinstance(execution_id, str) or execution_id not in wanted:
            continue
        if execution_id in seen:
            continue
        seen.add(execution_id)
        copied.append(execution_id)
        lines.append(line)
    if not lines:
        return []
    child_path = generate_ledger_path(root, child)
    child_path.parent.mkdir(parents=True, exist_ok=True)
    with child_path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")
    logger.info(
        "generate-ledger inherited parent=%s child=%s ids=%s",
        parent,
        child,
        copied,
    )
    return copied


__all__ = ["inherit_outstanding_generates"]
