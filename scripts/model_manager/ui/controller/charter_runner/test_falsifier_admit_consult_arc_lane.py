"""F-R5: consult admit must thread arc_lane into materialize_consult_packet (6490 class)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import dispatch
from scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch import (
    AdmitResult,
    FireAttemptOutcome,
    admit_consult_window,
)

pytestmark = pytest.mark.offline

_LAYER_G1_BODY = """\
# CHECKPOINT

## Next pickup
1. G1 — architecture · CONSULT_PENDING · consult_role: judgment_gap · executor_lane: judgment

## Steps
1. [ ] G1 — architecture verdict · [consult:judgment_gap]

Scoreboard: cortex://notes/system/threads/6467-charter-scoreboard.md
"""


@pytest.mark.asyncio
async def test_admit_consult_window_threads_arc_lane_to_materializer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6490 class defect: arc_lane must reach materialize_consult_packet."""
    captured: list[str] = []

    def capture_materialize(*_args, **kwargs):
        captured.append(str(kwargs.get("arc_lane")))
        return "layer-consult-packet"

    monkeypatch.setattr(
        dispatch,
        "materialize_consult_packet",
        capture_materialize,
    )
    monkeypatch.setattr(
        dispatch,
        "_fire_and_pointer",
        AsyncMock(
            return_value=AdmitResult(
                admitted=True,
                dispatch_id="6491",
                thread_id="6491",
                fire_attempt_outcome=FireAttemptOutcome.FIRED,
            )
        ),
    )

    row = RootLedgerRow(
        root_id="6489",
        status=RootStatus.IDLE,
        pickup_gid="G1",
        pickup_lane="consult",
        pickup_executor="cdp/fable",
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/layer-native-dogfood-g4-scoreboard.md",
    )
    turns = [
        {
            "turn_number": 1,
            "subject": "CHECKPOINT — layer G1 consult",
            "body": _LAYER_G1_BODY,
            "from_agent": "cursor",
        }
    ]
    caps = CapStore(intent_dir=tmp_path / "intent6489")

    result = await admit_consult_window(
        row=row,
        turns=turns,
        caps=caps,
        workspace_root=tmp_path,
        consult_role="judgment_gap",
        window_index=1,
        arc_lane="layer",
    )

    assert result.admitted is True
    assert captured == ["layer"]
