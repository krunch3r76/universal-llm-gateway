"""Hermetic tests for vision-digest boot injection (formatter + card gate)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_store.vision_digest import MAP_URI, build_vision_digest
from cortex_store.vision_digest_format import format_boot_card_md

from tools._boot_helpers._briefing_card import render_briefing_card
from tools._boot_helpers._vision_digest_section import (
    format_vision_digest_for_card,
    vision_digest_fetch_path,
)
from tools.cortex_named_tools._boot_data_fetch import (
    build_futures_spec,
    extract_boot_results,
)

_FIXTURE_MAP = """\
# Posture-stack foundation map

## Pillars

### Pillar 1 — Seat-lane / life liaison (who acts, where)

**Law:** web = life-matter + adjudication liaison on the life endpoint only; code-infra routes to checkout seats (cursor / cursor-sdk / grok-on-checkout); life→code escalation = teach + bus (`lane:life-to-code`), never a new life-intent verb.

- **SoT:** `decision:seat-lane-split-liaison-model` — ADOPTED; scoreboard `cortex://notes/system/threads/4917-charter-scoreboard.md`; skills `agent_skill:life-to-code-request-lane`.
- **Child arcs must not re-decide:** dual-endpoint membership/hide posture; web dual-attach (rejected).
- **Falsifier if ignored:** a child packet hands a life seat a `workspaces://` corpus it **cannot resolve**.

### Pillar 2 — Imprint · ring · endpoint provenance (what persists)

**Law:** checkpoint = forward imprint (re-presented at boot); transcript/graph = archive (recalled on demand); the two check each other — never trust an imprint blind on anything material.

- **SoT:** `decision:checkpoint-imprint-thread-as-ring` + design doc `cortex://notes/system/design/checkpoint-imprint-thread-as-ring.md`.
- **Child arcs must not re-decide:** imprint-vs-megatool (F1′ closed); checkpoint/scoreboard reconstitution shape.
- **Falsifier if ignored:** a seat linear-reads a root or resumes from narrative instead of CHECKPOINT+scoreboard.

### Pillar 3 — HTTP-first · OpenAPI as Semantic-Web heir (how surfaces are reached)

**Law:** HTTP is the agent tooling substrate with **served** typed-args schemas (OpenAPI, pulled-not-pushed) — NOT "HTTP replaces MCP"; MCP remains a client adapter, not the ontology.

- **SoT:** `decision:http-first-agent-substrate`; lineage memo `workspaces://universal-llm-gateway/tasks/asymmetric-bet-portfolio/http-agent-substrate-lineage.md`; https://www.rfc-editor.org/rfc/rfc8631.
- **Child arcs must not re-decide:** HTTP-replaces-MCP (rejected framing); served-not-pushed schema posture.
- **Falsifier if ignored:** new tool families mint as untyped megatools.

### Pillar 4 — Claim-record / WWP (what may be asserted)

**Law:** a factual artifact is a rendering of a **claim record** — every material claim carries an epistemic state (`backed | unverified | gap`) × a rendering state (`express | imply | omit_with_reason`).

- **SoT:** vision `cortex://notes/system/threads/5195-fable-writing-provenance/fable-vision-dialectic.md`; detector `todo:wwp-substrate-debt-gap-detector`.
- **Child arcs must not re-decide:** r1/r2/r3; two-axis + compatibility law.
- **Falsifier if ignored:** typed frictions recur at the pre-vision rate across ≥5 WWP-governed artifacts.
"""

_LAW_1 = (
    "web = life-matter + adjudication liaison on the life endpoint only; "
    "code-infra routes to checkout seats (cursor / cursor-sdk / grok-on-checkout); "
    "life→code escalation = teach + bus (`lane:life-to-code`), never a new life-intent verb."
)


@pytest.fixture()
def map_root(tmp_path: Path) -> Path:
    rel = MAP_URI.removeprefix("cortex://")
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_FIXTURE_MAP, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def digest_payload(map_root: Path) -> dict:
    digest = build_vision_digest(map_root)
    return digest.model_dump(mode="json")


def test_vision_digest_fetch_path() -> None:
    assert vision_digest_fetch_path() == "/api/v1/doctrine/vision-digest"


def test_format_boot_card_md_verbatim_laws(digest_payload: dict) -> None:
    md = format_boot_card_md(digest_payload)
    assert "## Vision digest" in md
    assert digest_payload["map_sha256"] in md
    assert MAP_URI in md
    assert _LAW_1 in md
    assert digest_payload["pillars"][2]["law_verbatim"] in md
    assert "Amendment" not in md


def test_format_vision_digest_for_card_404_soft_fail() -> None:
    assert format_vision_digest_for_card({"error": "not found", "status_code": 404}) is None
    assert format_vision_digest_for_card(None) is None
    assert format_vision_digest_for_card({"pillars": []}) is None


def test_build_futures_spec_claude_web_includes_digest() -> None:
    class _Recorder:
        def wrap(self, _name: str, fn):  # noqa: ANN001
            return fn

    spec = build_futures_spec("claude-web", {}, _Recorder())
    assert "vision_digest" in spec
    assert spec["vision_digest"][2] == "/api/v1/doctrine/vision-digest"


def test_build_futures_spec_cursor_omits_digest() -> None:
    class _Recorder:
        def wrap(self, _name: str, fn):  # noqa: ANN001
            return fn

    spec = build_futures_spec("claude-cursor", {}, _Recorder())
    assert "vision_digest" not in spec


def test_extract_boot_results_formats_digest(digest_payload: dict) -> None:
    raw = {
        "sessions": [],
        "threads": {"threads": []},
        "unread_toc": {},
        "todos": [],
        "reflective_journal": [],
        "recent_mentions": [],
        "skills": [],
        "recent_work": {},
        "async_dispatches": [],
        "vision_digest": digest_payload,
    }
    extracted = extract_boot_results("claude-web", raw, {})
    md = extracted["vision_digest_md"]
    assert md is not None
    assert "## Vision digest" in md
    assert digest_payload["map_sha256"] in md


def test_extract_boot_results_digest_error_omits_section() -> None:
    raw = {
        "sessions": [],
        "threads": {"threads": []},
        "unread_toc": {},
        "todos": [],
        "reflective_journal": [],
        "recent_mentions": [],
        "skills": [],
        "recent_work": {},
        "async_dispatches": [],
        "vision_digest": {"error": "HTTP 404", "status_code": 404},
    }
    extracted = extract_boot_results("claude-web", raw, {})
    assert extracted["vision_digest_md"] is None


def test_briefing_card_web_includes_vision_digest(digest_payload: dict) -> None:
    md = format_boot_card_md(digest_payload)
    card, _ = render_briefing_card(
        family="claude",
        agent="claude-web",
        vision_digest_md=md,
    )
    assert "## Vision digest" in card
    assert digest_payload["map_sha256"] in card
    assert _LAW_1 in card
    orient_idx = card.index("## Operator-facing duty")
    digest_idx = card.index("## Vision digest")
    assert orient_idx < digest_idx


def test_briefing_card_cursor_omits_vision_digest() -> None:
    card, _ = render_briefing_card(
        family="claude",
        agent="claude-cursor",
    )
    assert "## Vision digest" not in card


def test_formatter_map_sha256_matches_live_build(map_root: Path) -> None:
    digest = build_vision_digest(map_root)
    rel = MAP_URI.removeprefix("cortex://")
    expected = f"sha256:{hashlib.sha256((map_root / rel).read_bytes()).hexdigest()}"
    md = format_boot_card_md(digest)
    assert expected in md
    assert digest.pillars[0].law_verbatim in md
