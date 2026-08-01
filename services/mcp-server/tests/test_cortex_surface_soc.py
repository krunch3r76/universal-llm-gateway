"""Cortex MCP surface SoC — derive, render, gate matrix tests."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from _derive import _CORTEX_CENSUS_SIZE, derive_cortex_surface
from tool_access import endpoint_op_allowed, reset_endpoint_op_cache
from tools.cortex_named_tools import render_cortex_tool_description

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CANONICAL = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"

_LIFE_READ = {
    "activate",
    "analyze_impact",
    "assertion_get",
    "assertion_state",
    "assertions",
    "case_audit",
    "deadlines",
    "edge_traverse",
    "edge_types",
    "edges",
    "entities",
    "entities_by_content_hash",
    "entity_get",
    "fill_gaps",
    "frictions",
    "impact",
    "journal_read",
    "relationships",
    "render_subgraph",
    "resolve",
    "resolve_assertion_chunk",
    "review_queue",
    "rj_list",
    "rj_read",
    "search",
    "stats",
    "surface_forms",
    "tag_list",
    "todo_candidates",
    "walk_subgraph",
}
_LIFE_WRITE = {
    "assert",
    "assertion_update",
    "deadline_resolve",
    "edge_create",
    "edge_update",
    "entity_create",
    "entity_update",
    "friction",
    "friction_close",
    "observe",
    "recon_sidecar_write",
    "relationship_create",
    "relationship_update",
    "rj_consolidate",
    "rj_link",
    "rj_write",
    "supersede",
    "tag_assign",
    "tag_resolve",
    "thread_sidecar_write",
    "todo_close_sidecar",
}
_LIFE_SESSION = {
    "session_audit",
    "session_close",
    "session_close_preflight",
    "session_handoff_upsert",
}
_LIFE_GATE = {
    "doc_template",
    "doc_validate",
    "implement_ready_preflight",
    "todo_audit",
    "todo_distill_implement_gate",
}
_LIFE_ENUM = _LIFE_READ | _LIFE_WRITE | _LIFE_SESSION | _LIFE_GATE

_ADMIN_OPS = {
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
}


def test_life_enum_matches_bind_memo() -> None:
    life = derive_cortex_surface("life", _CANONICAL)
    assert set(life.ops_enum) == _LIFE_ENUM
    assert not _ADMIN_OPS & set(life.ops_enum)


def test_code_enum_excludes_admin() -> None:
    code = derive_cortex_surface("code", _CANONICAL)
    assert not _ADMIN_OPS & set(code.ops_enum)
    # ``digest`` is write+fol but fol is not yet reconciled for life (§ F-c);
    # code admits it, life does not — intentional asymmetry after 2026-07-14.
    assert set(code.ops_enum) == _LIFE_ENUM | {"digest"}


def test_census_completeness() -> None:
    life = derive_cortex_surface("life", _CANONICAL)
    assert len(life.families) == _CORTEX_CENSUS_SIZE


def test_census_conflict_raises() -> None:
    data = yaml.safe_load(_CANONICAL.read_text(encoding="utf-8"))
    for row in data.get("tools", []):
        if row.get("domain") == "cortex" and row.get("operation") == "assert":
            row["family"] = "read"
            break
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        tmp = Path(f.name)
    with pytest.raises(RuntimeError, match="conflict"):
        derive_cortex_surface("life", tmp)


def test_fol_descriptor_drops_life_write_op(monkeypatch: pytest.MonkeyPatch) -> None:
    data = yaml.safe_load(_CANONICAL.read_text(encoding="utf-8"))
    for row in data.get("tools", []):
        if row.get("domain") == "cortex" and row.get("operation") == "tag_assign":
            row.pop("fol_descriptor", None)
            break
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        tmp = Path(f.name)
    life = derive_cortex_surface("life", tmp)
    assert "tag_assign" not in life.ops_enum

    import _derive as derive_mod

    monkeypatch.setattr(derive_mod, "_DEFAULT_CANONICAL", tmp)
    reset_endpoint_op_cache()
    allowed, payload = endpoint_op_allowed("life", "cortex", "tag_assign")
    assert allowed is False
    assert payload is not None


@pytest.mark.parametrize("op", sorted(_ADMIN_OPS))
def test_life_admin_ops_rejected(op: str) -> None:
    allowed, payload = endpoint_op_allowed("life", "cortex", op)
    assert allowed is False
    assert payload is not None
    assert payload["family"] == "admin"
    assert payload["status_code"] == 422


def test_life_assert_allowed() -> None:
    allowed, payload = endpoint_op_allowed("life", "cortex", "assert")
    assert allowed is True
    assert payload is None


def test_code_audit_rejected_with_overflow_hint() -> None:
    allowed, payload = endpoint_op_allowed("code", "cortex", "audit")
    assert allowed is False
    assert payload is not None
    assert payload["family"] == "admin"
    assert "overflow" in payload["hint"].lower()


def test_non_cortex_tool_passes_through() -> None:
    allowed, payload = endpoint_op_allowed("life", "fs", "write")
    assert allowed is True
    assert payload is None


def test_life_descriptor_has_tier1_no_admin() -> None:
    desc = render_cortex_tool_description("life", canonical_yaml_path=_CANONICAL)
    assert "Tier-1" in desc
    for op in _ADMIN_OPS:
        assert f"  {op}:" not in desc


def test_code_descriptor_names_admin_overflow() -> None:
    desc = render_cortex_tool_description("code", canonical_yaml_path=_CANONICAL)
    assert "entity_merge" in desc
    assert "overflow" in desc.lower()
