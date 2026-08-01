"""OpenAPI-first MCP adapter machinery for git-integration-worker dispatch retirement."""

from ._ops import DISPATCH_OP_CATALOG_TOOL, GIW_DISPATCH_OPS
from ._route_map import (
    UNTYPEABLE_OPS,
    served_ops,
    typed_routes_from_openapi,
    unbound_dispatch_ops,
)
from .codegen import (
    AdapterManifest,
    check_generated_module,
    dry_run_generate,
    generate_adapter_manifest,
    write_generated_module,
)

__all__ = [
    "AdapterManifest",
    "DISPATCH_OP_CATALOG_TOOL",
    "GIW_DISPATCH_OPS",
    "UNTYPEABLE_OPS",
    "check_generated_module",
    "dry_run_generate",
    "generate_adapter_manifest",
    "served_ops",
    "typed_routes_from_openapi",
    "unbound_dispatch_ops",
    "write_generated_module",
]
