"""Relay clamp + pointer-over-excerpt tests (6294 turn 18 regression surface)."""

from __future__ import annotations

import json

from services.git_integration_worker.relay.closeout_relay_common import (
    build_ac_verdict_cell,
)

_DISPATCH = "auto-4f974d43d0de"
_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "partial",
        "files_created": [],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
    }
)

_SPEC_SNIPPET = (
    "# Agent-bus payload discipline\n\n"
    "M1 compensating parity gate — this spec markdown must not appear in ac_verdict.\n\n"
    + ("Lorem ipsum dolor sit amet. " * 120)
)


def _6294_sidecar() -> str:
    text = _SPEC_SNIPPET
    while len(text.encode("utf-8")) < 4027:
        text += " padding."
    return text


def test_build_ac_verdict_pointer_over_excerpt_for_addressable_provenance() -> None:
    sidecar = _6294_sidecar()
    provenance = f"cortex://notes/system/specs/example-{_DISPATCH}.md"
    cell = build_ac_verdict_cell(sidecar, provenance=provenance)
    assert "parse_failed" in cell
    assert provenance in cell
    assert "M1 compensating parity gate" not in cell
    assert "<br><br>" not in cell




























_CORTEX_POINTER = (
    "cortex://notes/system/threads/6329-cursor-sdk-closeout-auto-4f974d43d0de.md"
)












# --- row 11 — fenced appendix ellipsis + AC-1 alias theft (6655 bind) ---

_ROW11_FENCED_EVIDENCE_BLOCK = (
    "```python\n" + ("    observed_line = 'payload'\n" * 120) + "```"
)








