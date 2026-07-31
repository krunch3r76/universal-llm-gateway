"""Floor and wake-mapper drive set — bus enrollment only (Path B, agent-bus:6486)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.admission.typed_work_item import (
    TypedWorkItemAdmit,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    admit_work_item,
)
from scripts.model_manager.ui.controller.charter_runner.wake_consumer import (
    WakeConsumer,
)
from scripts.model_manager.ui.controller.charter_runner.wake_hub import (
    WakeDirtySet,
    WakeRootMapper,
)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_mapper_refresh_enrolled_bus_only() -> None:
    """Unenrolled ledger-open ghosts must not appear in mapper.enrolled."""

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6489"}, {"id": "6171"}]

    mapper = WakeRootMapper(enrolled)
    result = await mapper.refresh_enrolled()
    assert result == {"6489", "6171"}
    assert mapper.enrolled == {"6489", "6171"}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_floor_root_ids_excludes_unenrolled_ledger_open(
    tmp_path: Path,
) -> None:
    """Floor set ⊆ bus enrolled — unenrolled ledger-open ids excluded."""
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    conn = open_ledger_db(ledger_dir / "root-ledger.sqlite")

    def _seed(root_id: str) -> None:
        admit_work_item(
            conn,
            TypedWorkItemAdmit(
                root_id=root_id,
                pickup_gid="G1",
                pickup_lane="judgment",
                attendance="autonomous",
                scoreboard_uri=f"cortex://notes/system/threads/{root_id}-sb.md",
            ),
        )

    for ghost_id in ("6185", "6431", "6518"):
        _seed(ghost_id)
    _seed("6489")
    conn.close()

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6489"}]

    class _TickLoop:
        _caps = None
        _workspace_root = None
        _on_admit = None

    consumer = WakeConsumer(
        tick_loop=_TickLoop(),  # type: ignore[arg-type]
        dirty=WakeDirtySet(),
        mapper=WakeRootMapper(enrolled),
        floor_interval_s=60.0,
        services_healthy=lambda: True,
    )
    floor_ids = await consumer._floor_root_ids()
    assert floor_ids == ["6489"]
    assert "6185" not in floor_ids
    assert "6431" not in floor_ids
    assert "6518" not in floor_ids
