"""CI guards for OpenAPI-first MCP adapter (OMDR-STRANGLER-S136)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import _OP_SPECS
from cortex_store.main import create_app
from cortex_store.openapi_mcp.bijection import (
    assert_op_served_bijection,
    find_reachable_unserved_violations,
    served_operation_ids,
)
from cortex_store.openapi_mcp.census import build_four_bucket_census
from cortex_store.openapi_mcp.codegen import check_generated_module, dry_run_generate
from cortex_store.openapi_mcp.death_path import DEATH_PATH_GATE_DOC, death_path_gate_met
from cortex_store.openapi_mcp.schema_channel import SCHEMA_CHANNEL_DEFAULT


@pytest.mark.offline
def test_four_bucket_census_partitions_all_ops() -> None:
    census = build_four_bucket_census()
    assert census.total == len(_OP_SPECS)
    union = census.served | census.rb_only | census.neither | census.untypeable
    assert union == set(_OP_SPECS)


@pytest.mark.offline
def test_four_bucket_census_counts() -> None:
    census = build_four_bucket_census()
    assert len(census.served) == 20
    assert len(census.untypeable) == 4
    assert len(census.rb_only) == 36
    assert len(census.neither) == 20


@pytest.mark.offline
def test_assert_op_openapi_bijection() -> None:
    schema = create_app().openapi()
    assert_op_served_bijection(schema)


@pytest.mark.offline
def test_generator_dry_run_covers_served_ops() -> None:
    schema = create_app().openapi()
    manifest = dry_run_generate(schema)
    assert len(manifest.served_ops) == 20
    assert manifest.served_ops["assert"]["path"] == "/assertions"
    assert manifest.openapi_sha256


@pytest.mark.offline
def test_generated_manifest_matches_openapi() -> None:
    schema = create_app().openapi()
    assert check_generated_module(schema) is True


@pytest.mark.offline
def test_served_operation_ids_include_assert() -> None:
    ids = served_operation_ids()
    assert ids["assert"] == "create_assertion_assertions_post"


@pytest.mark.offline
def test_death_path_gate_requires_both_conditions() -> None:
    assert death_path_gate_met(served_parity=True, zero_non_adapter_traffic=True)
    assert not death_path_gate_met(served_parity=False, zero_non_adapter_traffic=True)
    assert not death_path_gate_met(served_parity=True, zero_non_adapter_traffic=False)
    assert "served-parity" in DEATH_PATH_GATE_DOC


@pytest.mark.offline
def test_schema_channel_defaults_to_generated_op() -> None:
    assert SCHEMA_CHANNEL_DEFAULT == "cortex_schema(op)"


@pytest.mark.offline
def test_reachable_unserved_violations_during_strangler() -> None:
    """Documents current strangler gap — live ops still lack typed routes."""
    live_ops = frozenset(
        {
            "entity_get",
            "assertion_get",
            "assert",
            "assertion_update",
            "entity_update",
            "search",
        }
    )
    violations = find_reachable_unserved_violations(live_ops)
    assert "entity_get" in violations
    assert "assert" not in violations
