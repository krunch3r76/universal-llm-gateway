"""Regression tests for arc 6637 propagation-parser integrity."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.propagate_admission import (
    admit_propagate_body,
)

# Verbatim-shaped body from arc 6637 turn 46: structured block + prose scope line
# with English "for" (must never mint service=for).
_TURN_46_BODY = """\
TYPE: DIRECTIVE
contract: propagate
arc: 6637 openapi-mcp-transition-finish / propagation-parser-integrity
scope: propagation sync_restart for rag, mcp, and git_integration_worker
effects_expected: propagation rows persisted; per-row execution status relayed inline
density: sparse
budget: ≤1

Restart landed code for rag first, then mcp, then git_integration_worker.

## propagation (§4 YAML — mint for harvest; not proven live in-band)

```yaml
propagation:
  - service: rag
    action: sync_restart
    safe_window: normal
    proof_class: served_artifact
  - service: mcp
    action: sync_restart
    safe_window: normal
    proof_class: client_visible
  - service: git_integration_worker
    action: sync_restart
    safe_window: normal
    proof_class: served_artifact
```
"""


def test_arc6637_turn46_never_yields_service_for():
    admission = admit_propagate_body(_TURN_46_BODY)
    services = [row.service for row in admission.rows]
    assert "for" not in services
    if admission.approved:
        assert services == ["rag", "mcp", "git_integration_worker"]
    else:
        assert admission.error is not None
        summary = str(admission.error.get("summary", ""))
        assert "propagation block rejected" in summary.lower()
        assert any(
            "invalid_safe_window" in flag or "normal" in flag
            for flag in admission.flags
        )


def test_arc6637_turn46_loud_rejection_names_safe_window_fault():
    admission = admit_propagate_body(_TURN_46_BODY)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "propagation_block_invalid"
    assert any("invalid_safe_window:normal" in flag for flag in admission.flags)
    assert admission.error["legal_safe_window"] == "harvest, standalone_ok, drain_required"


def test_propagation_block_present_rejects_shorthand_prose_fallback():
    import subprocess

    from deploy_identity.code_version import reset_code_version_cache_for_tests
    from universal_workspace import get_workspace_root

    reset_code_version_cache_for_tests()
    head = subprocess.run(
        ["git", "-C", str(get_workspace_root()), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout.decode().strip()
    body = _TURN_46_BODY.replace("safe_window: normal", "safe_window: harvest")
    body = body.replace(
        "  - service: rag",
        f"  - service: rag\n    code_ref: {head}",
        1,
    ).replace(
        "  - service: mcp",
        f"  - service: mcp\n    code_ref: {head}",
        1,
    ).replace(
        "  - service: git_integration_worker",
        f"  - service: git_integration_worker\n    code_ref: {head}",
        1,
    )
    admission = admit_propagate_body(body)
    assert admission.approved
    assert [row.service for row in admission.rows] == [
        "rag",
        "mcp",
        "git_integration_worker",
    ]
    assert "for" not in [row.service for row in admission.rows]


def test_shorthand_only_never_captures_for_from_prose_scope_line():
    body = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart for rag
effects_expected: row persisted
"""
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "propagate_rows_missing"


def test_unknown_service_blocked_at_admit_not_at_manage():
    body = """\
TYPE: DIRECTIVE
contract: propagate
effects_expected: row persisted

## propagation
```yaml
propagation:
  - service: not_a_real_service
    code_ref: abc
    proof_class: process_live
```
"""
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert any("unknown_service:not_a_real_service" in flag for flag in admission.flags)
