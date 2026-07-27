"""Unit tests for cursor-auto conclusion-side propagation RESIDUE."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_auto.episode_residue import (
    compose_closeout_body,
    residue_for_closeout,
    resolve_relay_residue,
)


def _closeout_payload(**fields: object) -> str:
    base: dict[str, object] = {
        "schema_version": 1,
        "status": "complete",
        "summary": "done",
        "source_ref": "test",
    }
    base.update(fields)
    return json.dumps(base)


def test_residue_sync_restart_for_giw_py_path():
    payload = _closeout_payload(
        files_modified=["services/git_integration_worker/cursor_auto/handler.py"],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "sync_restart: git_integration_worker" in block


def test_residue_install_plugin_for_plugin_rule_path():
    payload = _closeout_payload(
        files_modified=[
            "cursor-plugins/ulg-ecosystem/rules/mcp-tool-awareness_ulg.mdc",
        ],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "install_plugin" in block


def test_residue_sync_restart_before_install_plugin():
    payload = _closeout_payload(
        files_modified=[
            "cursor-plugins/ulg-ecosystem/rules/mcp-tool-awareness_ulg.mdc",
            "services/git_integration_worker/cursor_auto/handler.py",
        ],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    sync_idx = block.index("sync_restart:")
    install_idx = block.index("install_plugin")
    assert sync_idx < install_idx


def test_residue_none_for_docs_only_change():
    payload = _closeout_payload(
        files_modified=["docs/architecture/overview.md"],
    )
    assert residue_for_closeout(payload) is None


def test_residue_none_for_invalid_payload():
    assert residue_for_closeout("not json at all") is None


def test_residue_truncated_flag_when_total_exceeds_list():
    payload = _closeout_payload(
        files_modified=[
            "services/git_integration_worker/cursor_auto/a.py",
            "services/git_integration_worker/cursor_auto/b.py",
            "services/git_integration_worker/cursor_auto/c.py",
            "services/git_integration_worker/cursor_auto/d.py",
            "services/git_integration_worker/cursor_auto/e.py",
        ],
        files_modified_total=9,
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "paths truncated" in block


def test_residue_unresolved_for_unmapped_service_py():
    payload = _closeout_payload(
        files_modified=["services/cdp-ask/main.py"],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "unresolved:" in block
    assert "sync_restart" not in block


def test_residue_elides_overflow_instead_of_raising():
    payload = _closeout_payload(
        files_modified=[
            f"services/{directory}/main.py"
            for directory in (
                "agent-bus",
                "cortex-api",
                "event-service",
                "git_integration_worker",
                "mcp-server",
                "rag",
                "universal_cloud_proxy",
                "_universal-llm-gateway",
                "universal-stargate",
            )
        ]
        + ["cursor-plugins/ulg-ecosystem/rules/x_ulg.mdc"],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "elided" in block
    assert len(block.splitlines()) <= 12
    assert block.rstrip().endswith("Not auto-executed.")


def test_residue_prefers_propagation_residue_field():
    payload = _closeout_payload(
        files_modified=["docs/architecture/overview.md"],
        propagation_residue=[
            'sync_restart: git_integration_worker — manage(action="sync_restart", '
            'service="git_integration_worker")'
        ],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "sync_restart: git_integration_worker" in block


def test_compose_closeout_body_with_and_without_residue():
    base = "base"
    residue = "TYPE: RESIDUE\nx"
    assert compose_closeout_body(base, None) == base
    composed = compose_closeout_body(base, residue)
    assert composed.endswith(residue)
    assert composed == f"{base}\n\n{residue}"


def test_relay_residue_prefers_wrapper_over_prose_body():
    wrapper = _closeout_payload(
        files_modified=["docs/architecture/overview.md"],
        propagation_residue=[
            'sync_restart: git_integration_worker — manage(action="sync_restart", '
            'service="git_integration_worker")'
        ],
    )
    section2 = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:** PASS
**deltas_to_spec:** none
"""
    block = resolve_relay_residue(wrapper_body=wrapper, relay_body=section2)
    assert block is not None
    assert block.startswith("TYPE: RESIDUE")
    assert "sync_restart: git_integration_worker" in block


def test_relay_residue_falls_back_to_relay_body_when_no_wrapper():
    wrapperless = _closeout_payload(
        files_modified=["services/git_integration_worker/cursor_auto/handler.py"],
    )
    block = resolve_relay_residue(wrapper_body=None, relay_body=wrapperless)
    assert block is not None
    assert "sync_restart: git_integration_worker" in block
