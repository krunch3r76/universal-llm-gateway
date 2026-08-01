"""Tests for two-tier OpenAPI MCP manifest drift check."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from cortex_store.main import create_app
from cortex_store.openapi_mcp.codegen import (
    check_generated_module,
    check_generated_module_detailed,
    dry_run_generate,
    generate_adapter_manifest,
    write_generated_module,
)

from openapi_mcp.codegen import (
    compare_binding_drift,
    compare_schema_drift,
    compute_non_binding_fingerprints,
)


@pytest.mark.offline
def test_binding_drift_is_fatal() -> None:
    committed = {"assert": {"method": "POST", "path": "/assertions", "operation_id": "x"}}
    live = {}
    fatal = compare_binding_drift(committed, live)
    assert len(fatal) == 1
    assert "FATAL: binding lost for op 'assert'" in fatal[0]


@pytest.mark.offline
def test_schema_drift_with_fingerprints_is_warning_only() -> None:
    committed_fps = {"GET /health": "aaa"}
    live_fps = {"GET /health": "bbb"}
    warnings = compare_schema_drift(
        committed_sha256="dead",
        live_sha256="beef",
        committed_fingerprints=committed_fps,
        live_fingerprints=live_fps,
    )
    assert len(warnings) == 1
    assert warnings[0] == "WARNING: schema drift — changed GET /health"


@pytest.mark.offline
def test_missing_stamp_fatal_exit_code() -> None:
    schema = create_app().openapi()
    del schema["paths"]["/assertions"]["post"]["x-mcp"]
    result = check_generated_module_detailed(schema)
    assert result.exit_code == 1
    assert any("FATAL: binding lost for op 'assert'" in m for m in result.fatal_messages)
    assert check_generated_module(schema) is False


@pytest.mark.offline
def test_health_field_change_warning_not_fatal(tmp_path: Path) -> None:
    schema = create_app().openapi()
    manifest = generate_adapter_manifest(schema)
    target = tmp_path / "generated_adapter_manifest.py"
    write_generated_module(manifest, target=target)

    mutated = copy.deepcopy(schema)
    health = mutated["paths"]["/health"]["get"]
    health.setdefault("responses", {}).setdefault("200", {}).setdefault(
        "content", {}
    ).setdefault("application/json", {}).setdefault("schema", {})["properties"] = {
        "scratch_field": {"type": "string"},
    }

    live = generate_adapter_manifest(mutated)
    from openapi_mcp.codegen import check_manifest

    result = check_manifest(live, manifest_path=target)
    assert result.exit_code == 0
    assert not result.fatal_messages
    assert any("GET /health" in m for m in result.warning_messages)


@pytest.mark.offline
def test_compute_non_binding_fingerprints_excludes_stamped_routes() -> None:
    schema = create_app().openapi()
    served = dry_run_generate(schema).served_ops
    fps = compute_non_binding_fingerprints(schema, served)
    assert "GET /health" in fps
    assert "GET /assertions" not in fps
    assert "POST /assertions" not in fps
