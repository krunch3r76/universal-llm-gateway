"""Unit tests for cursor-auto conclusion-side propagation RESIDUE."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_auto.episode_residue import (
    compose_closeout_body,
    residue_for_closeout,
    resolve_relay_residue,
    structured_propagation_rows,
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
    assert "unresolved: services/cdp-ask/main.py" in block


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
    assert block.rstrip().endswith("install_plugin remains manual.")


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


def test_structured_rows_mint_one_row_per_consumer_for_tier_m_lib():
    payload = _closeout_payload(
        files_modified=["libs/claude_bundles/operator_proxy_tier_m.py"],
        evidence_uris={"git_refs": ["consumer-land-sha"]},
    )
    rows = structured_propagation_rows(payload)
    assert len(rows) == 1
    assert rows[0].service == "mcp"
    assert rows[0].code_ref == "consumer-land-sha"
    assert rows[0].safe_window == "standalone_ok"


def test_structured_rows_mint_deploy_identity_consumers():
    payload = _closeout_payload(
        files_modified=["libs/deploy_identity/code_version.py"],
        evidence_uris={"git_refs": ["deploy-identity-sha"]},
    )
    rows = structured_propagation_rows(payload)
    services = {row.service for row in rows}
    assert services == {"git_integration_worker", "mcp"}
    assert all(row.code_ref == "deploy-identity-sha" for row in rows)


def test_structured_rows_from_section4_markdown_block():
    payload = _closeout_payload(
        files_modified=["docs/readme.md"],
        evidence_uris={"git_refs": ["yaml-sha"]},
    )
    markdown = """\
## propagation (§4 YAML)

```yaml
propagation:
  - service: git_integration_worker
    code_ref: yaml-sha
    safe_window: drain_required
    proof: liveness probe
    proof_class: process_live
```
"""
    rows = structured_propagation_rows(payload, markdown_sources=[markdown])
    assert len(rows) == 1
    assert rows[0].service == "git_integration_worker"
    assert rows[0].proof_class == "process_live"


def test_residue_deploy_identity_consumers_not_libs_touched():
    payload = _closeout_payload(
        files_modified=["libs/deploy_identity/code_version.py"],
    )
    block = residue_for_closeout(payload)
    assert block is not None
    assert "sync_restart: git_integration_worker" in block
    assert "sync_restart: mcp" in block
    assert "libs_touched" not in block
    assert "lead must decide" not in block


def test_residue_none_for_lib_test_module():
    payload = _closeout_payload(
        files_modified=["libs/implement_admission/test_propagation_block_parser.py"],
    )
    assert residue_for_closeout(payload) is None


def test_structured_rows_none_for_lib_test_module():
    payload = _closeout_payload(
        files_modified=["libs/implement_admission/test_propagation_block_parser.py"],
        evidence_uris={"git_refs": ["test-only-sha"]},
    )
    assert structured_propagation_rows(payload) == ()
