"""Arc hub pointer on continuity hops — observed from lane thread tags.

When a private lane thread carries ``arc_hub:<entity_id>``, the hop body
names that hub entity and an assertion tip so the successor can bind arc
terms without inferring from prose.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)

ArcHubTipReader = Callable[[str], tuple[Any, str] | None]

_ARC_HUB_TAG_PREFIX = "arc_hub:"


@dataclass(frozen=True)
class ArcHubTip:
    """Observed arc hub binding for one continuity hop."""

    entity_id: str
    tip: str | None  # format a:<max_assertion_id>@<observed_at>
    status: str  # current | unknown


def parse_arc_hub_tag(tags: list[str] | None) -> str | None:
    """Return the first ``arc_hub:`` tag's entity id, or None when absent."""
    if not tags:
        return None
    for tag in tags:
        if not tag or not tag.startswith(_ARC_HUB_TAG_PREFIX):
            continue
        value = tag[len(_ARC_HUB_TAG_PREFIX) :].strip()
        return value or None
    return None


def _default_tip_reader(entity_id: str) -> tuple[Any, str] | None:
    """Read newest assertion id + observed_at for *entity_id* from cortex SQLite."""
    try:
        from cortex_store.db import check_cortex_db, cortex_conn

        if not check_cortex_db():
            return None
        conn = cortex_conn(read_only=True)
        try:
            row = conn.execute(
                "SELECT id, observed_at, created_at FROM assertions "
                "WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        observed = row["observed_at"] or row["created_at"]
        if observed is None or str(observed).strip() == "":
            return None
        return (row["id"], str(observed))
    except Exception:  # noqa: BLE001 — hop admission must not raise
        _logger.warning("arc hub tip read failed open", exc_info=True)
        return None


def assess_arc_hub(
    entity_id: str | None,
    *,
    tip_reader: ArcHubTipReader | None = None,
) -> ArcHubTip | None:
    """Classify arc hub tip for *entity_id*; None in, None out; never raises."""
    if not entity_id:
        return None
    reader = tip_reader if tip_reader is not None else _default_tip_reader
    try:
        result = reader(entity_id)
    except Exception:  # noqa: BLE001 — hop admission must not raise
        _logger.warning("arc hub tip_reader raised; failing open", exc_info=True)
        return ArcHubTip(entity_id=entity_id, tip=None, status="unknown")
    if result is None:
        return ArcHubTip(entity_id=entity_id, tip=None, status="unknown")
    max_assertion_id, observed_at = result
    tip = f"a:{max_assertion_id}@{observed_at}"
    return ArcHubTip(entity_id=entity_id, tip=tip, status="current")
