"""Unit tests for §4 propagation YAML block parsing."""

from __future__ import annotations

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
