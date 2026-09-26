"""Build-time OpenAPI → MCP adapter manifest generation for rag.

Thin rag-facade wrapper over the shared ``openapi_mcp.codegen`` library: it maps
``x-mcp``-bound routes (via ``typed_routes_from_openapi``) to served ops and
renders ``generated_adapter_manifest.py``. Called by
``scripts/openapi_mcp_codegen.py`` (``--write``/``--check``/dry run for
``--service rag``) and by ``libs/openapi_mcp/commit_snapshot.py`` for the
pre-commit drift gate. Output is deterministic (ops sorted by name).
"""

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
_FACADE = "rag"


def _served_ops_from_schema(openapi_schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    routes = typed_routes_from_openapi(openapi_schema)
    served: dict[str, dict[str, str]] = {}
    for op, route in sorted(routes.items()):
        served[op] = {
            "method": route.method,
            "path": route.path,
            "operation_id": route.operation_id,
        }
    return served


def generate_adapter_manifest(openapi_schema: dict[str, Any]) -> AdapterManifest:
    """Build megatool-facade adapter manifest from ``x-mcp``-bound OpenAPI routes.

    Served ops (method, path, operation_id per op name) come from the rag route
    map; the facade tool is fixed to ``rag``. Pure: no file I/O. Returns the
    in-memory ``AdapterManifest`` used by write, dry-run, and drift checks.
    """
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
    """Render the manifest to Python source and overwrite the manifest module.

    Side effect: writes UTF-8 text to ``target`` or, by default, the committed
    ``generated_adapter_manifest.py`` beside this module. Returns the path
    written.
    """
    path = target or _GENERATED
    path.write_text(render_generated_module(manifest), encoding="utf-8")
    return path


def dry_run_generate(openapi_schema: dict[str, Any]) -> AdapterManifest:
    """Offline-safe generator dry-run: build the rag manifest without writing.

    Equivalent to ``generate_adapter_manifest``; used by the codegen script to
    preview output without touching the committed module.
    """
    return generate_adapter_manifest(openapi_schema)


def check_generated_module_detailed(
    openapi_schema: dict[str, Any],
) -> ManifestCheckResult:
    """Two-tier drift check of the live schema against the committed manifest.

    Regenerates the manifest from ``openapi_schema`` and compares it with the
    on-disk module via ``check_manifest``: served-op binding drift (or a
    missing file) is FATAL, schema-hash/fingerprint drift is a warning only.
    Returns the ``ManifestCheckResult`` with both message lists.
    """
    live = generate_adapter_manifest(openapi_schema)
    return check_manifest(live, manifest_path=_GENERATED)


def check_generated_module(openapi_schema: dict[str, Any]) -> bool:
    """Return True when no binding (FATAL) drift vs on-disk manifest."""
    return check_generated_module_detailed(openapi_schema).ok
