"""OpenAPI-first MCP adapter machinery for cortex dispatch retirement.

Build-time codegen, four-bucket census, reachable↔served bijection checks,
and death-path gate constants for the OMDR-STRANGLER-S136 arc.
"""

from .bijection import (
    assert_op_served_bijection,
    find_reachable_unserved_violations,
    served_operation_ids,
)
from .census import FourBucketCensus, build_four_bucket_census, render_census_markdown
from .codegen import AdapterManifest, dry_run_generate, generate_adapter_manifest
from .death_path import DEATH_PATH_GATE_DOC, death_path_gate_met
from .schema_channel import SCHEMA_CHANNEL_DEFAULT, schema_channel_doc

__all__ = [
    "AdapterManifest",
    "DEATH_PATH_GATE_DOC",
    "FourBucketCensus",
    "SCHEMA_CHANNEL_DEFAULT",
    "assert_op_served_bijection",
    "build_four_bucket_census",
    "death_path_gate_met",
    "dry_run_generate",
    "find_reachable_unserved_violations",
    "generate_adapter_manifest",
    "render_census_markdown",
    "schema_channel_doc",
    "served_operation_ids",
]
