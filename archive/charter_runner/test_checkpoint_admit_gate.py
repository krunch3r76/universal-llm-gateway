"""Offline tests for checkpoint_admit_gate fail-closed hints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Phase 3: schema_skip_heal deleted — skip until ported off heal path.
pytestmark = pytest.mark.skip(reason="Phase 3: schema_skip_heal deleted")

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_checkpoint_for_admit,
)
from scripts.model_manager.ui.controller.charter_runner.admission import Decision

_GOOD = """TYPE: CHECKPOINT

## In one line
test

## Steps
1. [ ] G1 — do the thing

## Next pickup
1. G1 — do the thing · executor_lane: implement

## In-flight / WIP
none

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline (§ Standing root threads + § R12 completeness gate; cursor coding arc may also load orchestrator-workflow) → read cortex://notes/system/threads/x-scoreboard.md [+ scoreboard gated lane if named] → this is the latest CHECKPOINT (wave/in-flight/next above). Do not read the thread linearly. empty Next-pickup ≠ arc complete.
"""

_BOLD_LABELS = """TYPE: CHECKPOINT

**Live:** WIP=none.

**Next pickup:**
1. G1 — do the thing

**Do not:** stuff
"""


@pytest.mark.offline
def test_good_tick_checkpoint_admits() -> None:
    v = validate_checkpoint_for_admit(_GOOD)
    assert v.ok is True
    assert v.reason == "eligible"
    assert v.parsed is not None
    assert v.parsed.next_pickup_gated is True


@pytest.mark.offline
def test_bold_labels_fail_with_section_hint() -> None:
    v = validate_checkpoint_for_admit(_BOLD_LABELS)
    assert v.ok is False
    assert v.reason == "missing_sections"
    assert "## Next pickup" in v.fix_hint or "Next pickup" in v.fix_hint


@pytest.mark.offline
def test_missing_resume_fails() -> None:
    body = _GOOD.replace("— RESUME (any seat, no command):", "RESUME broken:")
    v = validate_checkpoint_for_admit(body)
    assert v.ok is False
    assert v.reason == "missing_resume_footer"
    assert "RESUME" in v.fix_hint


_MISSING_SIDECARS = """TYPE: CHECKPOINT

## In one line
test

## Steps
1. [ ] G1 — do the thing

## Next pickup
1. G1 — do the thing · executor_lane: implement

## In-flight / WIP
none

## Frictions
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline (§ Standing root threads + § R12 completeness gate; cursor coding arc may also load orchestrator-workflow) → read cortex://notes/system/threads/x-scoreboard.md [+ scoreboard gated lane if named] → this is the latest CHECKPOINT (wave/in-flight/next above). Do not read the thread linearly. empty Next-pickup ≠ arc complete.
"""


@pytest.mark.offline
@pytest.mark.asyncio
async def test_schema_skip_self_heal_on_second_missing_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "schema-skips"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.schema_skip_heal._schema_skip_dir",
        lambda: store,
    )
    post = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.bus_client.post_root_checkpoint",
        post,
    )
    verdict = validate_checkpoint_for_admit(_MISSING_SIDECARS)
    assert verdict.ok is False
    assert verdict.reason == "missing_sections"
    assert verdict.parsed is not None
    decision = Decision(
        False,
        "missing_sections",
        "5918",
        checkpoint={"turn_number": 10, "body": _MISSING_SIDECARS},
        parsed=verdict.parsed,
    )
    first = await try_self_heal_schema_skip(decision, caps=CapStore())
    assert first is False
    assert post.await_count == 0
    second = await try_self_heal_schema_skip(decision, caps=CapStore())
    assert second is True
    assert post.await_count == 1
    body = post.await_args.kwargs["body"]
    repaired = validate_checkpoint_for_admit(body)
    assert repaired.ok is True, (repaired.reason, repaired.fix_hint)
