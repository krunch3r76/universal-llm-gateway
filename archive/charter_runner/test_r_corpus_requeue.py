"""Holder-repair requeue after consecutive stale_r_corpus_sha refusals."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_checkpoint_for_admit,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
    RCorpusShaResult,
    clear_r_corpus_refusals,
    refuse_stale_r_admit,
    verify_r_corpus_sha,
)

_RESUME = (
    "— RESUME (any seat, no command): load agent-bus-discipline "
    "(§ Standing root threads + § R12 completeness gate) → read scoreboard "
    "→ this is the latest CHECKPOINT. empty Next-pickup ≠ arc complete."
)

_STALE_PIN = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

_CHECKPOINT = f"""\
# CHECKPOINT — r-corpus stale pin

## Anchor
- Author: worker
- Scoreboard: cortex://notes/system/threads/5918-charter-scoreboard.md

## Steps
1. [x] G1 — question
2. [x] G2 — answer
3. [ ] G3 — R-admit

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit · executor_lane: judgment

## Frictions
_None this window._

## Sidecars
- Dense spec: cortex://notes/system/specs/missing-dense.md · spec_sha256:{_STALE_PIN}

## What happened (plain)
Worker posted CHECKPOINT with a pin that matches no file.

{_RESUME}
"""


@pytest.fixture
def refusal_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "r-corpus-refusals"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.r_corpus_sha._refusal_store_dir",
        lambda: store,
    )
    return store


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.skip(reason="Phase 3: supervisor never authors CHECKPOINTs on stale_r_corpus_sha")
async def test_refuse_stale_r_admit_posts_repair_on_second_refusal(
    refusal_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = verify_r_corpus_sha(_CHECKPOINT)
    assert result.ok is False
    assert result.reason == "stale_r_corpus_sha"
    assert result.sub_reason == "unreadable"

    post = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.bus_client.post_root_checkpoint",
        post,
    )
    events = MagicMock()
    events.emit_manage_charter_tick_root_skipped = AsyncMock()
    log = MagicMock()
    checkpoint = {"turn_number": 42, "body": _CHECKPOINT}

    await refuse_stale_r_admit(
        root_id="5918",
        checkpoint=checkpoint,
        result=result,
        events_module=events,
        log=log,
    )
    assert post.await_count == 0

    await refuse_stale_r_admit(
        root_id="5918",
        checkpoint=checkpoint,
        result=result,
        events_module=events,
        log=log,
    )
    assert post.await_count == 1
    kwargs = post.await_args.kwargs
    body = kwargs["body"]
    verdict = validate_checkpoint_for_admit(body)
    assert verdict.ok is True, (verdict.reason, verdict.fix_hint)
    parsed = parse_checkpoint(body)
    joined = " | ".join(parsed.next_pickup)
    assert "G3a" in joined
    assert "executor_lane: judgment" in joined
    assert parsed.consult_pending is False
    # Machine must not rewrite Sidecars pins — live_hex is State advisory only.
    sidecars = body.split("## Sidecars", 1)[-1].split("##", 1)[0]
    assert "live_hex" not in sidecars
    assert "spec_sha256:" not in sidecars
    clear_r_corpus_refusals("5918")
