"""Unit tests for structured propagation row parsing and coercion."""

from __future__ import annotations

from implement_admission.propagation_row import (
    PropagationRow,
    rows_from_closeout_payload,
    rows_from_lib_consumers,
    rows_from_residue_lines,
)


def test_structured_propagation_wins_over_legacy():
    payload = {
        "propagation": [
            {
                "service": "mcp",
                "action": "sync_restart",
                "code_ref": "sha-structured",
                "safe_window": "standalone_ok",
                "proof": "GET /health → code_version == code_ref",
                "proof_class": "client_visible",
            }
        ],
        "propagation_residue": [
            'sync_restart: git_integration_worker — manage(action="sync_restart", service="git_integration_worker")'
        ],
    }
    rows, skipped, prose = rows_from_closeout_payload(payload)
    assert len(rows) == 1
    assert rows[0].service == "mcp"
    assert rows[0].code_ref == "sha-structured"
    assert skipped == []
    assert prose is False


def test_legacy_residue_coerces_to_rows():
    rows, skipped = rows_from_residue_lines(
        ['sync_restart: mcp — manage(action="sync_restart", service="mcp")'],
        code_ref="legacy-sha",
    )
    assert len(rows) == 1
    assert rows[0].service == "mcp"
    assert rows[0].code_ref == "legacy-sha"
    assert rows[0].safe_window == "standalone_ok"
    assert skipped == []


def test_prose_only_runtime_land_sets_advisory():
    payload = {
        "propagation_residue": ["libs_touched: libs/foo.py — lead decides"],
        "files_modified": ["libs/implement_admission/spec.py"],
        "evidence_uris": {"git_refs": ["sha-prose"]},
    }
    rows, _skipped, prose = rows_from_closeout_payload(payload)
    assert prose is True
    assert rows == []


def test_row_model_requires_service_and_code_ref():
    row = PropagationRow(service="mcp", code_ref="abc")
    assert row.proof_class == "client_visible"
    assert row.safe_window == "standalone_ok"


def test_rows_from_lib_consumers_skips_test_module():
    rows = rows_from_lib_consumers(
        ["libs/implement_admission/test_propagation_block_parser.py"],
        code_ref="sha-test-skip",
    )
    assert rows == []
