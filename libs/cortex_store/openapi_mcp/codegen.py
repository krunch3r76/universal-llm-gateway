"""Build-time OpenAPI → MCP adapter manifest generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._route_map import typed_routes_from_openapi

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GENERATED = Path(__file__).resolve().parent / "generated_adapter_manifest.py"


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    openapi_sha256: str
    served_ops: dict[str, dict[str, str]]
    facade_tool: str = "cortex"


def _openapi_sha256(openapi_schema: dict[str, Any]) -> str:
    payload = json.dumps(openapi_schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_adapter_manifest(
    openapi_schema: dict[str, Any],
) -> AdapterManifest:
    """Build megatool-facade adapter manifest from ``x-mcp``-bound OpenAPI routes."""
    routes = typed_routes_from_openapi(openapi_schema)
    served: dict[str, dict[str, str]] = {}
    for op, route in sorted(routes.items()):
        served[op] = {
            "method": route.method,
            "path": route.path,
            "operation_id": route.operation_id,
        }
    return AdapterManifest(
        openapi_sha256=_openapi_sha256(openapi_schema),
        served_ops=served,
    )


def render_generated_module(manifest: AdapterManifest) -> str:
    """Render committed adapter manifest module source."""
    lines = [
        '"""Generated MCP adapter manifest — do not edit by hand.',
        "",
        "Regenerate:",
        "  python scripts/openapi_mcp_codegen.py --write",
        "  python scripts/openapi_mcp_codegen.py --check",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f'OPENAPI_SHA256 = "{manifest.openapi_sha256}"',
        f'FACADE_TOOL = "{manifest.facade_tool}"',
        "SERVED_OPS: dict[str, dict[str, str]] = {",
    ]
    for op, meta in manifest.served_ops.items():
        lines.append(f'    "{op}": {{')
        for key, val in meta.items():
            lines.append(f'        "{key}": "{val}",')
        lines.append("    },")
    lines.extend(["}", ""])
    return "\n".join(lines)


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


def check_generated_module(openapi_schema: dict[str, Any]) -> bool:
    """Return True when on-disk manifest matches live OpenAPI."""
    if not _GENERATED.is_file():
        return False
    expected = render_generated_module(generate_adapter_manifest(openapi_schema))
    return _GENERATED.read_text(encoding="utf-8") == expected
