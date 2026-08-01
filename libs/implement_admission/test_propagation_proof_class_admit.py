"""Tests for propagation admit proof_class validation (arc 6637 AC3)."""

from __future__ import annotations

from implement_admission.propagation_admit_validation import (
    legal_proof_classes,
    validate_proof_class,
)
from implement_admission.propagation_row import rows_from_parsed_block
from services.git_integration_worker.cursor_auto.propagate_admission import (
    admit_propagate_body,
)


def test_mcp_served_artifact_blocked_at_admit_names_legal_classes():
    body = """\
TYPE: DIRECTIVE
contract: propagate
effects_expected: row persisted

## propagation
```yaml
propagation:
  - service: mcp
    code_ref: d3e17d54
    safe_window: standalone_ok
    proof_class: served_artifact
```
"""
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "propagation_block_invalid"
    assert any(
        "invalid_proof_class:served_artifact" in flag for flag in admission.flags
    )
    assert any("legal for mcp: client_visible, process_live" in flag for flag in admission.flags)


def test_rag_served_artifact_admits():
    rows, flags = rows_from_parsed_block(
        [
            {
                "service": "rag",
                "code_ref": "abc",
                "proof_class": "served_artifact",
            }
        ]
    )
    assert flags == []
    assert len(rows) == 1
    assert rows[0].proof_class == "served_artifact"


def test_legal_proof_classes_mcp_excludes_served_artifact():
    assert legal_proof_classes("mcp") == frozenset({"client_visible", "process_live"})
    assert validate_proof_class("mcp", "served_artifact") is not None
    assert validate_proof_class("mcp", "client_visible") is None


def test_legal_proof_classes_cortex_api_includes_served_artifact():
    legal = legal_proof_classes("cortex_api")
    assert "served_artifact" in legal
    assert "client_visible" not in legal
    assert validate_proof_class("cortex_api", "served_artifact") is None
