"""Manifest derivation from canonical.yaml (D7).

Co-located with server.py per operator decision D7: derivation logic lives
here, not in a separate loader module. This is a helper for derivation and
startup-time coherence checks — not a typed data layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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


def derive_all_canonical_tool_names(
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> set[str]:
    """Return every tool name declared in canonical.yaml (flat + dispatcher shapes)."""
    data = _load_registry(canonical_yaml_path)
    tools: list[dict[str, Any]] = data.get("tools", [])
    names: set[str] = set()
    for entry in tools:
        flat = entry.get("flat_call_shape", {}).get("tool")
        disp = entry.get("dispatcher_call_shape", {}).get("tool")
        if flat:
            names.add(flat)
        if disp:
            names.add(disp)
    return names


def validate_registered_tool_coherence(
    registered_tool_names: set[str],
    allowlist: frozenset[str] = frozenset(),
    canonical_yaml_path: Path = _DEFAULT_CANONICAL,
) -> list[str]:
    """Return registered tool names absent from canonical.yaml and not allowlisted.

    ∀ t ∈ registered_tool_names: t ∈ canonical_names ∨ t ∈ allowlist ∨ violation.
    """
    canonical_names = derive_all_canonical_tool_names(canonical_yaml_path)
    return sorted(registered_tool_names - canonical_names - allowlist)


def run_startup_tool_coherence_checks(
    primary_tools: set[str],
    registered_tool_names: set[str],
    *,
    allowlist: frozenset[str],
) -> None:
    """Forward + inverse coherence at boot (primary hard-fail; inverse advisory)."""
    from mcp_events import record  # noqa: PLC0415

    fwd_violations = validate_primary_tools_coherence(primary_tools)
    if fwd_violations:
        raise RuntimeError(
            f"Primary tools absent from canonical registry: {fwd_violations}"
        )

    drift = validate_registered_tool_coherence(
        registered_tool_names, allowlist=allowlist
    )
    if drift:
        _logger.warning(
            "Tool coherence drift — registered but undeclared in canonical.yaml: %s",
            drift,
        )
        record("mcp.server.tool.coherence.drift", tools=drift, count=len(drift))
    else:
        record(
            "mcp.server.tool.coherence.ok",
            registered=len(registered_tool_names),
        )


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

    Per-op skill routing: grouping collapses one tool per op into one tool per
    domain, so a per-op ``skill_uri`` that diverges from the domain's top-level
    binding would be lost (the canonical-cortex case: ``session_close`` →
    ``agent_skill:session-close`` discarded under ``agent_skill:cortex``). When an
    op's ``skill_uri`` is non-empty AND differs from the group binding, it is
    recovered under ``op_skills: {op: skill_uri}`` (sorted; key omitted when no op
    diverges). Ops whose binding equals the domain's lose no information and are
    NOT duplicated into ``op_skills``. ``op_skills`` is consumed only by the boot
    briefing renderer; it does NOT enter the ``tools/list`` payload (built by
    FastMCP from registered dispatcher Tool objects), so it cannot churn the
    prompt cache.

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
        op = t["dispatcher_call_shape"]["dispatch_value"]
        domain_map[domain]["ops"].append(op)
        op_skill = t.get("skill_uri", "")
        if op_skill and op_skill != domain_map[domain]["skill_uri"]:
            domain_map[domain].setdefault("op_skills", {})[op] = op_skill

    manifest = list(domain_map.values())
    # Sort ops + op_skills within each domain for byte stability.
    for entry in manifest:
        entry["ops"].sort()
        if "op_skills" in entry:
            entry["op_skills"] = dict(sorted(entry["op_skills"].items()))

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
    tool_names = sorted(e["tool_name"] for e in manifest)
    names_sha256 = hashlib.sha256(json.dumps(tool_names).encode()).hexdigest()
    from mcp_events import record  # noqa: PLC0415

    record(
        "mcp.server.claude.manifest.boot",
        domain_count=len(manifest),
        names_sha256=names_sha256,
    )
    return manifest


# ── Cortex per-surface op partition (Option C) ────────────────────────────────

Surface = Literal["life", "code"]
_CORTEX_CENSUS_SIZE = 72
_FOL_MARKERS = frozenset({"∀", "∃", "⟹", "¬", "∈"})


@dataclass(frozen=True)
class CortexSurfaceSpec:
    """Derived cortex op partition for one MCP endpoint surface."""

    ops_enum: tuple[str, ...]
    families: dict[str, str]
    tier1_rows: dict[str, str]


def _reconciled_fol(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and any(m in stripped for m in _FOL_MARKERS)


def _curated_cortex_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in data.get("tools", []) if t.get("domain") == "cortex"]


def _build_cortex_families(data: dict[str, Any]) -> dict[str, str]:
    """Classify every census op exactly once; raise on conflict or gap."""
    cf = data.get("cortex_families") or {}
    uncurated: dict[str, list[str]] = cf.get("uncurated") or {}
    families: dict[str, str] = {}

    for fam, ops in uncurated.items():
        for op in ops:
            if op in families:
                raise RuntimeError(
                    f"Cortex census duplicate op {op!r} in uncurated family {fam!r}"
                )
            families[op] = fam

    for row in _curated_cortex_rows(data):
        op = row["dispatcher_call_shape"]["dispatch_value"]
        row_family = row.get("family")
        if not row_family:
            continue
        if op in families and families[op] != row_family:
            raise RuntimeError(
                f"Cortex family conflict for {op!r}: "
                f"uncurated={families[op]!r} curated={row_family!r}"
            )
        families[op] = row_family

    if len(families) != _CORTEX_CENSUS_SIZE:
        raise RuntimeError(
            f"Cortex census size mismatch: got {len(families)}, "
            f"expected {_CORTEX_CENSUS_SIZE}"
        )
    return families


def _endpoint_visibility(row: dict[str, Any]) -> list[str] | None:
    vis = row.get("endpoint_visibility")
    if vis is None:
        return None
    if isinstance(vis, list):
        return vis
    return None


def _fol_by_op(data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in _curated_cortex_rows(data):
        op = row["dispatcher_call_shape"]["dispatch_value"]
        fol = (row.get("fol_descriptor") or "").strip()
        if fol:
            out[op] = fol
    return out


def _visibility_by_op(data: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in _curated_cortex_rows(data):
        op = row["dispatcher_call_shape"]["dispatch_value"]
        vis = _endpoint_visibility(row)
        if vis is not None:
            out[op] = vis
    return out


def _ops_enum_for_surface(
    surface: Surface,
    *,
    families: dict[str, str],
    fol_by_op: dict[str, str],
    defaults: dict[str, list[str]],
    visibility_by_op: dict[str, list[str]],
) -> tuple[str, ...]:
    admitted: list[str] = []
    for op in sorted(families):
        fam = families[op]
        if fam == "admin":
            continue
        endpoints = visibility_by_op.get(op) or defaults.get(fam, [])
        if surface == "life" and "life" not in endpoints:
            continue
        if surface == "code" and "code" not in endpoints:
            continue
        if surface == "life" and fam == "write" and not _reconciled_fol(
            fol_by_op.get(op, "")
        ):
            continue
        admitted.append(op)
    return tuple(admitted)


def derive_cortex_surface(
    surface: Surface,
    canonical_yaml_path: Path | None = None,
) -> CortexSurfaceSpec:
    """Derive per-surface cortex op enum + family map from canonical.yaml.

    Init-time only. Enforces 72-op census completeness and family conflicts.
    """
    path = canonical_yaml_path or _DEFAULT_CANONICAL
    data = _load_registry(path)
    families = _build_cortex_families(data)
    cf = data.get("cortex_families") or {}
    defaults: dict[str, list[str]] = cf.get("defaults") or {}
    fol_by_op = _fol_by_op(data)
    visibility_by_op = _visibility_by_op(data)

    ops_enum = _ops_enum_for_surface(
        surface,
        families=families,
        fol_by_op=fol_by_op,
        defaults=defaults,
        visibility_by_op=visibility_by_op,
    )

    tier1_rows = {
        op: fol
        for op, fol in fol_by_op.items()
        if families.get(op) in {"write", "session"} and _reconciled_fol(fol)
    }

    _logger.info(
        "cortex_surface_boot surface=%s op_count=%d",
        surface,
        len(ops_enum),
    )
    return CortexSurfaceSpec(
        ops_enum=ops_enum,
        families=dict(families),
        tier1_rows=tier1_rows,
    )


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
