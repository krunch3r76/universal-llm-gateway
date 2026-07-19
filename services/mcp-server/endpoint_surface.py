"""Dual-endpoint surface policy — primary tool sets and overflow catalog filtering."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from _derive import _DEFAULT_CANONICAL, _load_registry, derive_claude_manifest

Surface = Literal["life", "code"]

MCP_LIFE_PATH = "/mcp/life"
MCP_CODE_PATH = "/mcp/code"

# D7 register-function → surface: code-only overflow families (no canonical domain row).
_CODE_ONLY_OVERFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "quality_gate",
        "query_observability_preview",
        "topology",
        "sql",
        "sqlite_execute",
        "sqlite_list_databases",
        "sqlite_schema",
        "js_analyze",
        "model_status",
        "list_models",
        "pipeline_consult",
        "google_imagine",
        "grok_imagine",
        "openai_imagine",
    }
)

# Cortex named admin overflow tools (F-g) — code catalog only.
_CORTEX_ADMIN_OVERFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "cortex_chunk_create",
        "cortex_chunk_get",
        "cortex_staging_list",
        "cortex_staging_reject",
        "cortex_staging_batch_approve",
        "cortex_surface_form_create",
        "cortex_surface_form_lookup",
    }
)


def surface_from_path(path: str) -> Surface | None:
    """Map HTTP path prefix to MCP endpoint surface."""
    if path == MCP_LIFE_PATH or path.startswith(f"{MCP_LIFE_PATH}/"):
        return "life"
    if path == MCP_CODE_PATH or path.startswith(f"{MCP_CODE_PATH}/"):
        return "code"
    return None


@lru_cache(maxsize=4)
def _domain_endpoints_map(
    canonical_yaml_path: str = str(_DEFAULT_CANONICAL),
) -> dict[str, list[str]]:
    data = _load_registry(Path(canonical_yaml_path))
    block = data.get("domain_endpoints") or {}
    default = list(block.get("default") or ["life", "code"])
    exceptions: dict[str, list[str]] = block.get("exceptions") or {}
    return {"__default__": default, **exceptions}


@lru_cache(maxsize=4)
def _tool_domain_map(
    canonical_yaml_path: str = str(_DEFAULT_CANONICAL),
) -> dict[str, str]:
    data = _load_registry(Path(canonical_yaml_path))
    mapping: dict[str, str] = {}
    for row in data.get("tools", []):
        domain = row.get("domain")
        if not domain:
            continue
        flat = (row.get("flat_call_shape") or {}).get("tool")
        disp = (row.get("dispatcher_call_shape") or {}).get("tool")
        if flat:
            mapping[str(flat)] = str(domain)
        if disp:
            mapping[str(disp)] = str(domain)
    return mapping


def domain_endpoints_for(
    domain: str, *, canonical_yaml_path: Path | None = None
) -> list[str]:
    """Return endpoint visibility for a registry domain."""
    path_key = str(canonical_yaml_path or _DEFAULT_CANONICAL)
    table = _domain_endpoints_map(path_key)
    return list(table.get(domain, table["__default__"]))


def derive_surface_primary_tools(
    surface: Surface,
    canonical_yaml_path: Path | None = None,
) -> frozenset[str]:
    """Primary ``tools/list`` names for one MCP mount."""
    path = canonical_yaml_path or _DEFAULT_CANONICAL
    data = _load_registry(path)
    domains: list[str] = list(
        (data.get("surface_primary_domains") or {}).get(surface) or []
    )
    manifest = derive_claude_manifest(path)
    domain_to_tool = {e["domain"]: e["tool_name"] for e in manifest}
    tools: set[str] = set()
    for domain in domains:
        tool_name = domain_to_tool.get(domain, domain)
        tools.add(tool_name)
    return frozenset(tools)


def overflow_tool_allowed_on_surface(
    tool_name: str,
    surface: Surface,
    *,
    canonical_yaml_path: Path | None = None,
) -> bool:
    """Whether an overflow catalog row may appear on ``tool_search`` for *surface*."""
    if tool_name == "skill_suggest":
        return False
    if surface == "code":
        return True
    if tool_name.startswith("git_"):
        return False
    if tool_name in _CODE_ONLY_OVERFLOW_TOOLS:
        return False
    if tool_name in _CORTEX_ADMIN_OVERFLOW_TOOLS:
        return False
    path_key = str(canonical_yaml_path or _DEFAULT_CANONICAL)
    domain = _tool_domain_map(path_key).get(tool_name)
    if domain:
        return "life" in domain_endpoints_for(
            domain, canonical_yaml_path=canonical_yaml_path
        )
    return True


def filter_overflow_metadata_for_surface(
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]],
    surface: Surface,
    *,
    canonical_yaml_path: Path | None = None,
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Drop surface-disallowed overflow rows (life-only filters + hidden tombstones)."""
    return {
        name: meta
        for name, meta in overflow_metadata.items()
        if overflow_tool_allowed_on_surface(
            name, surface, canonical_yaml_path=canonical_yaml_path
        )
    }
