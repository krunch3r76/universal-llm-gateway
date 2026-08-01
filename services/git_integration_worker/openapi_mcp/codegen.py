"""Build-time OpenAPI → MCP adapter manifest generation for git-integration-worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openapi_mcp.codegen import (
    AdapterManifest,
    ManifestCheckResult,
    build_adapter_manifest,
    check_manifest,
    render_generated_module,
)

from ._route_map import typed_routes_from_openapi

_GENERATED = Path(__file__).resolve().parent / "generated_adapter_manifest.py"
_FACADE = "giw"


def _served_ops_from_schema(openapi_schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    routes = typed_routes_from_openapi(openapi_schema)
    served: dict[str, dict[str, str]] = {}
    for op, route in sorted(routes.items()):
        served[op] = {
            "method": route.method,
            "path": route.path,
            "operation_id": route.operation_id,
            "tool": route.tool,
        }
    return served


def generate_adapter_manifest(openapi_schema: dict[str, Any]) -> AdapterManifest:
    """Build multi-tool adapter manifest from ``x-mcp``-bound OpenAPI routes."""
    return build_adapter_manifest(
        openapi_schema,
        _served_ops_from_schema(openapi_schema),
        facade_tool=_FACADE,
    )


def write_generated_module(
    manifest: AdapterManifest,
    *,
    target: Path | None = None,
) -> Path:
    """Write generated manifest module; returns path written."""
    path = target or _GENERATED
    path.write_text(render_generated_module(manifest), encoding="utf-8")
    return path


def dry_run_generate(openapi_schema: dict[str, Any]) -> AdapterManifest:
    """Offline-safe generator dry-run (no write)."""
    return generate_adapter_manifest(openapi_schema)


def check_generated_module_detailed(
    openapi_schema: dict[str, Any],
) -> ManifestCheckResult:
    """Two-tier drift check against the committed manifest."""
    live = generate_adapter_manifest(openapi_schema)
    return check_manifest(live, manifest_path=_GENERATED)


def check_generated_module(openapi_schema: dict[str, Any]) -> bool:
    """Return True when no binding (FATAL) drift vs on-disk manifest."""
    return check_generated_module_detailed(openapi_schema).ok
