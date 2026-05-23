"""Grok flat-manifest derivation from canonical.yaml (D7 / G2).

∀ canonical_yaml_path: derive_grok_manifest(canonical_yaml_path) ⟹
  deterministic list[ToolManifestEntry] for /mcp/grok.

Pure function — same YAML in, same manifest out, byte-stable (entries
sorted by canonical_name). Init-time / module-load only per plan PB-3
(¬per-request).

Co-located with server.py per operator decision D7: derivation logic lives
here, not in a separate loader module. This is a helper for derivation and
startup-time coherence checks — not a typed data layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from universal_logging import get_logger

_logger = get_logger(__name__)

ToolManifestEntry = dict[str, Any]
DispatcherManifestEntry = dict[str, Any]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CANONICAL = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"


def _load_registry(canonical_yaml_path: Path) -> dict[str, Any]:
    """Load and parse canonical.yaml; raise ImportError if PyYAML unavailable."""
    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "PyYAML is required for manifest derivation (pip install pyyaml)"
        ) from exc
    raw = canonical_yaml_path.read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw)
    return data


def derive_grok_manifest(
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> list[ToolManifestEntry]:
    """Return flat tool manifest for /mcp/grok derived from canonical.yaml.

    ∀ entry T ∈ registry.tools where 'mcp_grok' ∈ T.seat_visibility:
      emit {canonical_name, name, description, inputSchema, domain,
            mandate_safety, cache_priority, skill_uri}.
    Sorted by canonical_name for byte-stable output.

    Pure function. Init-time / module-load only. ¬per-request.
    """
    data = _load_registry(canonical_yaml_path)
    tools: list[dict[str, Any]] = data.get("tools", [])

    manifest: list[ToolManifestEntry] = []
    for t in sorted(tools, key=lambda x: x["canonical_name"]):
        if "mcp_grok" not in t.get("seat_visibility", []):
            continue
        entry: ToolManifestEntry = {
            "canonical_name": t["canonical_name"],
            "name": t["flat_call_shape"]["tool"],
            "description": t.get("fol_descriptor", "").strip(),
            "inputSchema": t.get("json_schema", {}),
            "domain": t["domain"],
            "mandate_safety": t.get("mandate_safety", ""),
            "cache_priority": t.get("cache_priority", ""),
            "skill_uri": t.get("skill_uri", ""),
        }
        manifest.append(entry)

    return manifest


def derive_mcp_dispatcher_domain_set(
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> set[str]:
    """Return set of domain names with at least one mcp-visible tool.

    Used by Risk-4 CI gate and startup coherence check.
    """
    data = _load_registry(canonical_yaml_path)
    tools: list[dict[str, Any]] = data.get("tools", [])
    return {t["domain"] for t in tools if "mcp" in t.get("seat_visibility", [])}


def validate_primary_tools_coherence(
    primary_tools: set[str],
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> list[str]:
    """Return list of tools in primary_tools not found as domains in canonical.yaml.

    ∀ t ∈ primary_tools: t ∈ registry_domains ∨ violation.

    Used for Risk-4 startup-time check in server.py:
      violations = validate_primary_tools_coherence(_PRIMARY_TOOLS)
      if violations: raise RuntimeError(...)
    """
    registry_domains = derive_mcp_dispatcher_domain_set(canonical_yaml_path)
    return sorted(primary_tools - registry_domains)


# ── Claude /mcp dispatcher manifest (Phase D) ─────────────────────────────────

_CLAUDE_TOKEN = "mcp_claude"
_CLAUDE_CAP = 24
_CLAUDE_FLOOR: frozenset[str] = frozenset({"cortex", "agent_bus", "fs", "dispatch"})

_ClaudeManifestCache: list[DispatcherManifestEntry] | None = None


def derive_claude_manifest(
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> list[DispatcherManifestEntry]:
    """Return dispatcher-grouped manifest for Claude /mcp surface derived from canonical.yaml.

    ∀ domain D ∈ canonical.yaml where ∃ tool T: D.seat_visibility ∋ _CLAUDE_TOKEN:
      emit one DispatcherManifestEntry {domain, tool_name, ops, description, skill_uri}.
    Sorted by domain name for byte-stable output.

    Floor assertion: {cortex, agent_bus, fs, dispatch} ⊆ derived_domains ∨ RuntimeError.
    Cap assertion: len(manifest) ≤ 24 ∨ raises RuntimeError at init time (D3).
    Pure function. Init-time / module-load only. ¬per-request.
    """
    data = _load_registry(canonical_yaml_path)
    tools: list[dict[str, Any]] = data.get("tools", [])

    # Group by dispatcher tool name; filter by mcp_claude visibility.
    domain_map: dict[str, dict[str, Any]] = {}
    for t in sorted(tools, key=lambda x: x["domain"]):
        if _CLAUDE_TOKEN not in t.get("seat_visibility", []):
            continue
        domain = t["domain"]
        dispatcher_tool = t["dispatcher_call_shape"]["tool"]
        if domain not in domain_map:
            domain_map[domain] = {
                "domain": domain,
                "tool_name": dispatcher_tool,
                "ops": [],
                "description": t.get("fol_descriptor", "").strip(),
                "skill_uri": t.get("skill_uri", ""),
            }
        domain_map[domain]["ops"].append(t["dispatcher_call_shape"]["dispatch_value"])

    manifest = list(domain_map.values())
    # Sort ops within each domain for byte stability.
    for entry in manifest:
        entry["ops"].sort()

    # A1: floor assertion — required domains must be present.
    derived_domains = {e["domain"] for e in manifest}
    missing_floor = _CLAUDE_FLOOR - derived_domains
    if missing_floor:
        raise RuntimeError(
            f"Claude manifest floor breached: required domains absent: "
            f"{sorted(missing_floor)}. Check seat_visibility in canonical.yaml."
        )

    # M2: cap assertion — sorted domain list in error for debuggability.
    if len(manifest) > _CLAUDE_CAP:
        raise RuntimeError(
            f"Claude manifest cap exceeded: {len(manifest)} > {_CLAUDE_CAP} (D3). "
            f"Domains: {sorted(e['domain'] for e in manifest)}. "
            f"Remove mcp_claude from seat_visibility before starting the server."
        )

    _logger.info(
        "claude_manifest_boot domain_count=%d domains=%s",
        len(manifest),
        sorted(e["domain"] for e in manifest),
    )
    return manifest


def get_claude_manifest(
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> list[DispatcherManifestEntry]:
    """Return the cached Claude manifest, deriving it at first call.

    ∀ calls after boot: returns the same module-level cache (init-time only).

    NOTE: The cache is keyed on first-call success, not on the path argument.
    Production always passes ``_DEFAULT_CANONICAL``; if a caller ever needs a
    different YAML path mid-run, they must invalidate ``_ClaudeManifestCache``
    manually or call ``derive_claude_manifest`` directly.
    """
    global _ClaudeManifestCache
    if _ClaudeManifestCache is None:
        _ClaudeManifestCache = derive_claude_manifest(canonical_yaml_path)
    return _ClaudeManifestCache
