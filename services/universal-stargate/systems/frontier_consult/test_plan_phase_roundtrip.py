"""Phase 2 — hermetic live round-trip ADMIT test for the plan_phase deck adapter.

Drives the admission core (resolve_source_ref_to_packet) with a stub cortex
reader + a real on-disk deck under tmp_path, asserting the materialized packet
ADMITS with the deck embedded verbatim. No live Stargate, no cortex HTTP, no
IDE-thread side effect — the todo's true acceptance bar
(plan-deck-handoff-packet-adapter §9).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission.materialize import packet_is_sufficient
from implement_admission.source_ref import SourceRefError

from .handoff import validate_packet
from .implement_admission_bridge import resolve_source_ref_to_packet

SLUG = "phase2-roundtrip"
ENTITY_ID = f"plan_phase:{SLUG}/phase-1"
SOURCE_REF = f"plan:{SLUG}/phase-1"

DECK = """# Phase 1: roundtrip sample

**Expected Executor**: Composer 2.5 Thinking
**Optional Consultation**: None

## Objective

Exercise the live round-trip admit path.

## Tasks

### Task 1: embed

**BEFORE:**

```python
old = 1
```

**AFTER:**

```python
new = 2
```

## Verification

- [ ] packet admits
- [ ] deck embedded verbatim

## Expected Files

Create: none | Modify: `libs/implement_admission/adapter.py` | Delete: none
"""

_SIX_BLOCKS = (
    "<scope>",
    "<invariants>",
    "<task_guidance>",
    "<corpus>",
    "<output_format>",
    "<mcp_capabilities>",
)


class _StubCortex:
    """Duck-typed cortex reader: entity_get returns a plan_phase entity dict."""

    def entity_get(self, entity_id: str, **kwargs: object) -> dict:
        assert entity_id == ENTITY_ID
        return {
            "id": entity_id,
            "name": "Phase 1: roundtrip sample",
            "content_hash": "sha256:stubcontent",
            "attributes": {
                "phase_number": 1,
                "content_hash": "sha256:stubcontent",
                "required_skills": [],
            },
        }


def _write_deck(root: Path, body: str = DECK) -> Path:
    deck_dir = root / "tmp" / "prompts" / SLUG
    deck_dir.mkdir(parents=True, exist_ok=True)
    path = deck_dir / "phase-1-roundtrip.md"
    path.write_text(body, encoding="utf-8")
    return path


def _packet_text(root: Path, packet_path: str) -> str:
    direct = root / packet_path
    if direct.is_file():
        return direct.read_text(encoding="utf-8")
    stripped = root / packet_path.removeprefix("universal-llm-gateway/")
    return stripped.read_text(encoding="utf-8")


def test_plan_phase_roundtrip_admits_with_deck_embedded(tmp_path: Path) -> None:
    _write_deck(tmp_path)

    result = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_StubCortex(), workspaces_root=tmp_path
    )

    assert result.gated is False
    assert result.packet_path
    assert result.implement_spec_hash
    assert result.implement_spec_hash.startswith("sha256:")

    packet = _packet_text(tmp_path, result.packet_path)
    for tag in _SIX_BLOCKS:
        assert tag in packet
    assert packet_is_sufficient(packet)
    # True admission gate (not just materializer sufficiency): raises
    # FrontierEndpointError if the packet is not admissible for an implement handoff.
    validate_packet(
        request_id="phase2-roundtrip",
        packet_path=result.packet_path,
        to_agent="claude-cursor",
        handoff_contract="implement",
        workspaces_root=tmp_path,
    )
    # Deck embedded verbatim (not the old metadata-only [:20] truncation).
    assert "--- PHASE DECK (verbatim) ---" in packet
    assert "**BEFORE:**" in packet
    assert "new = 2" in packet


def test_deck_edit_changes_implement_spec_hash(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    first = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_StubCortex(), workspaces_root=tmp_path
    )

    _write_deck(tmp_path, body=DECK + "\n## Extra\n\nchanged.\n")
    second = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_StubCortex(), workspaces_root=tmp_path
    )

    assert first.implement_spec_hash != second.implement_spec_hash


def test_missing_deck_hard_fails(tmp_path: Path) -> None:
    # No deck on disk → dispatch lane MUST hard-fail (A1), not degrade.
    with pytest.raises(SourceRefError) as exc:
        resolve_source_ref_to_packet(
            SOURCE_REF, cortex=_StubCortex(), workspaces_root=tmp_path
        )
    assert exc.value.code == "phase_doc_not_found"


_DENSE_SPEC = """# Dense roundtrip spec

## Problem

No on-disk deck.

## Non-goals

None.

## Provenance

| Source | Role |
|---|---|
| spec | authoritative |

## Touch-points

- `libs/implement_admission/adapter.py`

## Bound design decisions

| Fork | Decision |
|---|---|
| 1 | resolved |

## Implementation guidance

Materialize from dense spec.

## Acceptance criteria

1. Corpus contains exact dense-spec body.

## Verification

- [ ] packet admits
- [ ] hash binds body

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""


class _DenseSpecCortex:
    def entity_get(self, entity_id: str, **kwargs: object) -> dict:
        assert entity_id == ENTITY_ID
        return {
            "id": entity_id,
            "name": "Phase 1: roundtrip sample",
            "content_hash": "sha256:stubcontent",
            "attributes": {
                "phase_number": 1,
                "content_hash": "sha256:stubcontent",
                "required_skills": [],
                "dense_spec_uri": (
                    f"cortex://notes/system/specs/{SLUG}.md"
                ),
            },
        }


def _write_dense_spec(root: Path, body: str = _DENSE_SPEC) -> Path:
    spec_dir = root / "notes" / "system" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / f"{SLUG}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_plan_phase_dense_spec_fallback_admits_without_deck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _write_dense_spec(tmp_path)

    result = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_DenseSpecCortex(), workspaces_root=tmp_path
    )

    assert result.gated is False
    assert result.packet_path
    packet = _packet_text(tmp_path, result.packet_path)
    assert "--- PHASE DECK (verbatim) ---" in packet
    assert _DENSE_SPEC in packet
    assert packet_is_sufficient(packet)


def test_plan_phase_dense_spec_hash_changes_when_body_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _write_dense_spec(tmp_path)
    first = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_DenseSpecCortex(), workspaces_root=tmp_path
    )

    _write_dense_spec(tmp_path, body=_DENSE_SPEC + "\n## Extra\n\nchanged.\n")
    second = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_DenseSpecCortex(), workspaces_root=tmp_path
    )

    assert first.implement_spec_hash != second.implement_spec_hash


def test_plan_phase_deck_wins_when_both_deck_and_dense_spec_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _write_deck(tmp_path)
    _write_dense_spec(tmp_path)

    result = resolve_source_ref_to_packet(
        SOURCE_REF, cortex=_DenseSpecCortex(), workspaces_root=tmp_path
    )

    packet = _packet_text(tmp_path, result.packet_path)
    assert "--- PHASE DECK (verbatim) ---" in packet
    assert "**BEFORE:**" in packet
    assert "Dense roundtrip spec" not in packet
