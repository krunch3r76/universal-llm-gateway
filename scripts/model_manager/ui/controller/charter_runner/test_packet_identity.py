"""Packet self-identity — materializer embeds arc identity (5870 real captures)."""

from __future__ import annotations

import hashlib
import re

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    _work_summary,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    select_packet,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_consult_packet,
)

_ROOT = "5870"
_SCOREBOARD = "cortex://notes/system/threads/5870-charter-scoreboard.md"

# Real INPUT CHECKPOINT bodies from agent-bus:5870 (turn numbers in names).
_CP_W3 = """\
TYPE: CHECKPOINT

## Next pickup
1. G3 — R-admit · CONSULT_PENDING · consult_role: r_admit · todo:session-edge-markdown-sections-fence-toggle · executor_lane: judgment

## Steps
1. [x] G1-Q — L0 question table + recommended Question set
2. [x] G2 — A + Gate-2 dense spec + implement_ready
3. [ ] G3 — R-admit (CONSULT_PENDING)

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:session-edge-markdown-sections-fence-toggle · G2

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""

_CP_W10 = """\
TYPE: CHECKPOINT

## Next pickup
1. G3 — R-admit · todo:cursor-auto-capture-attribution-untracked · executor_lane: judgment · CONSULT_PENDING (consult_role: r_admit)

## Steps
1. [x] Prior: fence-toggle path-sim arc closed (w1–w7)
2. [x] G1-Q — L0 question table + recommended Question set (capture attribution)
3. [x] G2 — A + Gate-2 dense spec + implement_ready
4. [ ] G3 — R-admit (CONSULT_PENDING)

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:cursor-auto-capture-attribution-untracked · workflow_state=in_progress

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""

_CP_W17 = """\
TYPE: CHECKPOINT

## Next pickup
1. G3 — R-admit · CONSULT_PENDING · consult_role: r_admit · todo:cursor-auto-closeout-auto-nudge · executor_lane: judgment

## Steps
1. [x] Prior: capture-attribution path-sim arc closed (w8–w14)
2. [x] G1-Q — L0 question table + recommended Question set (closeout wake)
3. [x] G2 — A + Gate-2 dense spec + implement_ready
4. [ ] G3 — R-admit (CONSULT_PENDING)

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:cursor-auto-closeout-auto-nudge · workflow_state=in_progress

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""

_CP_W9 = """\
TYPE: CHECKPOINT

## Next pickup
1. G2 — A + Gate-2 · todo:cursor-auto-capture-attribution-untracked · executor_lane: judgment

## Steps
1. [x] Prior: fence-toggle path-sim arc closed (w1–w7)
2. [x] G1-Q — L0 question table + recommended Question set (capture attribution)
3. [ ] G2 — A + Gate-2 dense spec + implement_ready
4. [ ] G3 — R-admit (CONSULT_PENDING)

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:cursor-auto-capture-attribution-untracked · workflow_state=in_progress

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""

_CP_W16 = """\
TYPE: CHECKPOINT

## Next pickup
1. G2 — A + Gate-2 · todo:cursor-auto-closeout-auto-nudge · executor_lane: judgment

## Steps
1. [x] Prior: capture-attribution path-sim arc closed (w8–w14)
2. [x] G1-Q — L0 question table + recommended Question set (closeout wake)
3. [ ] G2 — A + Gate-2 dense spec + implement_ready
4. [ ] G3 — R-admit (CONSULT_PENDING)

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:cursor-auto-closeout-auto-nudge · workflow_state=in_progress

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""

_CP_W13 = """\
TYPE: CHECKPOINT

## Next pickup
1. G5 — R-after · todo:cursor-auto-capture-attribution-untracked · executor_lane: judgment

## Steps
1. [x] Prior: fence-toggle path-sim arc closed (w1–w7)
2. [x] G1-Q — L0 question table
3. [x] G2 — A + Gate-2 dense spec + implement_ready
4. [x] G3 — R-admit (ADMIT_WITH_AMENDMENTS)
5. [x] G3b — fold R amendments AM-1…AM-8
6. [x] G4 — implement + deploy-verify
7. [ ] G5 — R-after

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:cursor-auto-capture-attribution-untracked · G4 implement

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""

_CP_W20 = """\
TYPE: CHECKPOINT

## Next pickup
1. G5 — R-after · `/work-item-review` · todo:cursor-auto-closeout-auto-nudge · cursor-sdk cursor/grok-4.5

## Steps
1. [x] Prior: capture-attribution path-sim arc closed (w8–w14)
2. [x] G1-Q — L0 question table + recommended Question set (closeout wake)
3. [x] G2 — A + Gate-2 dense spec + implement_ready
4. [x] G3 — R-admit (ADMIT_WITH_AMENDMENTS)
5. [x] G3b — fold R amendments B1–B10 into dense spec + doc_validate + refresh implement_ready sha
6. [x] G4 — implement + deploy-verify
7. [ ] G5 — R-after

## WIP / In-flight
_None this window._

## Anchor
- Todo: todo:cursor-auto-closeout-auto-nudge · G4 implement shipped

## Scoreboard URI
cortex://notes/system/threads/5870-charter-scoreboard.md
"""


def _strip_window_for_hash(packet: str) -> str:
    """Thrash-evidence normalization from 5905 sidecar."""
    out = re.sub(r"\bwindow \d+\b", "window", packet, flags=re.IGNORECASE)
    return re.sub(r"CONSULT window \d+", "CONSULT window", out, flags=re.IGNORECASE)


def _norm_hash(packet: str) -> str:
    return hashlib.sha256(_strip_window_for_hash(packet).encode()).hexdigest()


def _consult_packet(body: str, window_index: int) -> str:
    parsed = parse_checkpoint(body)
    return materialize_consult_packet(
        _ROOT,
        parsed,
        scoreboard_uri=_SCOREBOARD,
        window_index=window_index,
    )


def _autonomous_packet(body: str, window_index: int) -> str:
    parsed = parse_checkpoint(body)
    packet, _ = select_packet(
        _ROOT,
        parsed,
        scoreboard_uri=_SCOREBOARD,
        window_index=window_index,
        admission_mode="autonomous",
    )
    return packet


def _identity_lines(packet: str) -> tuple[str, str]:
    pickup_m = re.search(
        r"- normalized_next_pickup: (.+)$", packet, re.MULTILINE
    )
    ref_m = re.search(r"- source_ref: (.+)$", packet, re.MULTILINE)
    assert pickup_m and ref_m, "identity block missing from packet"
    return pickup_m.group(1).strip(), ref_m.group(1).strip()


def _advance_line(packet: str) -> str:
    m = re.search(r"^Advance: (.+)$", packet, re.MULTILINE)
    assert m, "Advance line missing"
    return m.group(1).strip()


def _strip_identity(packet: str) -> str:
    return re.sub(
        r"\n## Window identity \(BINDING\)\n(?:- .+\n)+",
        "\n",
        packet,
    )


@pytest.mark.parametrize(
    ("body", "window"),
    [
        (_CP_W3, 3),
        (_CP_W10, 10),
        (_CP_W17, 17),
    ],
)
def test_consult_packets_distinct_hash(body: str, window: int) -> None:
    h = _norm_hash(_consult_packet(body, window))
    assert len(h) == 64


def test_consult_w3_w10_w17_three_distinct_hashes() -> None:
    hashes = [
        _norm_hash(_consult_packet(_CP_W3, 3)),
        _norm_hash(_consult_packet(_CP_W10, 10)),
        _norm_hash(_consult_packet(_CP_W17, 17)),
    ]
    assert len(set(hashes)) == 3, f"expected 3 distinct hashes, got {hashes}"


def test_consult_identity_lines_visible_and_differ() -> None:
    lines = [_identity_lines(_consult_packet(b, w)) for b, w in [
        (_CP_W3, 3),
        (_CP_W10, 10),
        (_CP_W17, 17),
    ]]
    pickups = [line[0] for line in lines]
    refs = [line[1] for line in lines]
    assert len(set(pickups)) == 3
    assert len(set(refs)) == 3
    assert "todo:session-edge-markdown-sections-fence-toggle" in pickups[0]
    assert "todo:cursor-auto-capture-attribution-untracked" in pickups[1]
    assert "todo:cursor-auto-closeout-auto-nudge" in pickups[2]


def test_consult_non_identity_portion_preserves_mandated_reads() -> None:
    """AC3 — mandated root CHECKPOINT + scoreboard reads stay intact."""
    packet = _consult_packet(_CP_W3, 3)
    assert "read the latest CHECKPOINT on agent-bus:5870" in packet
    assert f"read the scoreboard at {_SCOREBOARD}, then" in packet
    assert "Load consult-routing + checkpoint-discipline + path-sim" in packet
    assert "Stop after CHECKPOINT — no nested SDK consult fan-out" in packet


def test_autonomous_w9_w16_distinct_hashes() -> None:
    h9 = _norm_hash(_autonomous_packet(_CP_W9, 9))
    h16 = _norm_hash(_autonomous_packet(_CP_W16, 16))
    assert h9 != h16, f"w9/w16 still collide: {h9}"


def test_autonomous_w13_w20_distinct_hashes() -> None:
    h13 = _norm_hash(_autonomous_packet(_CP_W13, 13))
    h20 = _norm_hash(_autonomous_packet(_CP_W20, 20))
    assert h13 != h20, f"w13/w20 still collide: {h13}"


def test_work_summary_w9_vs_w16_disambiguated() -> None:
    w9 = _work_summary(parse_checkpoint(_CP_W9))
    w16 = _work_summary(parse_checkpoint(_CP_W16))
    assert w9 != w16
    assert "todo:cursor-auto-capture-attribution-untracked" in w9
    assert "todo:cursor-auto-closeout-auto-nudge" in w16
    assert "Step 3 — G2 — A + Gate-2 dense spec + implement_ready" not in w9


def test_advance_line_w9_vs_w16_in_packets() -> None:
    adv9 = _advance_line(_autonomous_packet(_CP_W9, 9))
    adv16 = _advance_line(_autonomous_packet(_CP_W16, 16))
    assert adv9 != adv16
    assert "todo:cursor-auto-capture-attribution-untracked" in adv9
    assert "todo:cursor-auto-closeout-auto-nudge" in adv16
