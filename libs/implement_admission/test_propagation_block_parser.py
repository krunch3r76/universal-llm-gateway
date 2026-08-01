"""Unit tests for §4 propagation YAML block parsing."""

from __future__ import annotations

from implement_admission.propagation_admit_validation import LEGAL_SAFE_WINDOW_LIST
from implement_admission.propagation_block_parser import (
    parse_propagation_block,
    parse_propagation_yaml_document,
)
from implement_admission.propagation_row import rows_from_parsed_block

_6329_BLOCK = """\
## propagation (§4 YAML — mint for harvest; not proven live in-band)

```yaml
propagation:
  - service: git_integration_worker
    action: sync_restart
    code_ref: a28906fc180db15916f61f00eb61a633b7781689
    safe_window: drain_required
    hazard: closeout_relay
    reason: "enqueue wire-skew tolerance"
    proof: "GET /api/v1/git/cursor-auto/liveness → code_version == code_ref"
    proof_class: process_live
  - service: mcp
    action: sync_restart
    code_ref: a28906fc180db15916f61f00eb61a633b7781689
    safe_window: standalone_ok
    proof: "GET /health → code_version == code_ref"
    proof_class: client_visible
```
"""


def test_parse_6329_propagation_block_two_rows():
    rows, flags = parse_propagation_block(_6329_BLOCK)
    assert flags == []
    assert len(rows) == 2
    assert rows[0]["service"] == "git_integration_worker"
    assert rows[0]["proof_class"] == "process_live"
    assert rows[1]["proof_class"] == "client_visible"


def test_missing_proof_class_is_flagged_not_defaulted():
    yaml_text = """\
propagation:
  - service: mcp
    code_ref: abc
    proof: probe
"""
    rows, flags = parse_propagation_yaml_document(yaml_text)
    assert rows == []
    assert any("missing_proof_class" in flag for flag in flags)


def test_rows_from_parsed_block_strict_proof_class():
    rows, flags = rows_from_parsed_block(
        [{"service": "mcp", "code_ref": "sha", "proof_class": "client_visible"}]
    )
    assert flags == []
    assert len(rows) == 1
    assert rows[0].proof_class == "client_visible"


def test_omitted_code_ref_not_invalid_shape():
    """Documented shorthand permits omitting code_ref (defaults to HEAD at mint)."""
    yaml_text = """\
propagation:
  - service: git_integration_worker
    action: sync_restart
    proof_class: process_live
"""
    rows, flags = parse_propagation_yaml_document(yaml_text)
    assert "propagation_row_0_invalid_shape" not in flags
    assert len(rows) == 1
    materialized, mat_flags = rows_from_parsed_block(rows)
    assert mat_flags == []
    assert len(materialized) == 1
    assert materialized[0].service == "git_integration_worker"


def test_invalid_shape_deciding_line_is_normalize_row_code_ref_on_head():
    """AC1 — missing code_ref on committed parser is invalid_shape via _normalize_row."""
    yaml_text = """\
propagation:
  - service: rag
    proof_class: client_visible
"""
    rows, flags = parse_propagation_yaml_document(yaml_text)
    assert rows == [{"service": "rag", "proof_class": "client_visible"}]
    assert "propagation_row_0_invalid_shape" not in flags


def test_safe_window_normal_rejected_with_legal_values():
    rows, flags = rows_from_parsed_block(
        [
            {
                "service": "mcp",
                "code_ref": "abc",
                "safe_window": "normal",
                "proof_class": "client_visible",
            }
        ]
    )
    assert rows == []
    assert flags == [
        f"propagation_row_0_invalid_safe_window:normal; legal: {LEGAL_SAFE_WINDOW_LIST}"
    ]

