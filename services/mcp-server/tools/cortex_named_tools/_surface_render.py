"""Per-surface cortex tool descriptor rendering (Option C)."""

from __future__ import annotations

from typing import Literal

from _derive import CortexSurfaceSpec, derive_cortex_surface

Surface = Literal["life", "code"]
_CODE_BUDGET = 2048
_LIFE_BUDGET = 6144

_ADMIN_OVERFLOW_OPS = (
    "assemble_transcript",
    "audit",
    "edge_retire",
    "entities_bulk_upsert",
    "entity_merge",
    "entity_rekey",
    "entity_retype",
    "pinned_deliverable_write",
    "prose_fact_scan",
    "register_skill_substrate",
    "relationship_delete",
    "relationships_bulk_upsert",
)

_CONSTANT_CORE = """\
Cortex knowledge system — entities, assertions, relationships, edges, journals.

tool: operation name (enum on wire schema).
arguments: JSON-encoded object string (e.g. '{"entity_id": "type:slug"}').
Large/quote-heavy payloads (session_close transcript_md / session_summary_md /
handoff_prompt): write payload to a file and pass a file-path param
(session_summary_md_path / transcript_jsonl_path / handoff_source_path), or use
the /agent-bus CLI. See agent_skill:cortex.
"""


def _assert_budget(text: str, surface: Surface) -> None:
    size = len(text.encode("utf-8"))
    limit = _CODE_BUDGET if surface == "code" else _LIFE_BUDGET
    if size > limit:
        raise RuntimeError(
            f"cortex descriptor byte budget exceeded for {surface!r}: "
            f"{size} > {limit}"
        )


def _tier1_block(spec: CortexSurfaceSpec) -> str:
    lines = ["Write/session Tier-1 cores (reconciled fol_descriptor):"]
    for op in sorted(spec.tier1_rows):
        fam = spec.families.get(op, "")
        if fam not in {"write", "session"}:
            continue
        core = spec.tier1_rows[op].replace("\n", " ").strip()
        lines.append(f"  {op}: {core}")
    return "\n".join(lines)


def _code_admin_routing() -> str:
    return (
        "Admin ops (entity_merge, entity_rekey, entity_retype, audit, …) "
        "are not on this tools/list — use dispatch/tool_search overflow on the code seat."
    )


def render_cortex_tool_description(
    surface: Surface,
    *,
    canonical_yaml_path=None,
) -> str:
    """Render the cortex MCP tool docstring for one endpoint surface."""

    from _derive import _DEFAULT_CANONICAL

    path = canonical_yaml_path or _DEFAULT_CANONICAL
    spec = derive_cortex_surface(surface, path)

    parts = [_CONSTANT_CORE.rstrip()]
    if surface == "life":
        parts.append(_tier1_block(spec))
    else:
        parts.append(_code_admin_routing())

    text = "\n\n".join(parts) + "\n"
    _assert_budget(text, surface)
    return text
