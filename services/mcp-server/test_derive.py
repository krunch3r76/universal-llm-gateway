"""Derivation tests for derive_claude_manifest (Phase D) against live canonical.yaml."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _derive import derive_claude_manifest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CANONICAL_YAML = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"
_TESTDATA = Path(__file__).parent / "testdata"


def _serialise(manifest: list) -> str:
    return json.dumps(manifest, sort_keys=True, indent=2)


# ── Phase D: derive_claude_manifest tests ─────────────────────────────────────


def test_derive_claude_manifest_count() -> None:
    """D1: derived Claude manifest returns exactly one entry per domain."""
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    assert (
        len(manifest) == 20
    )  # update if domains change. dispatch overflow-only; team_dispatch standalone.
    tool_names = [e["tool_name"] for e in manifest]
    assert len(tool_names) == len(set(tool_names)), "duplicate tool_names in manifest"


def test_derive_claude_manifest_cap() -> None:
    """D3: cap enforcement — ≤ 24 domains. Uses real canonical.yaml; should pass."""
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    assert len(manifest) <= 24, f"Cap exceeded: {len(manifest)} > 24"


def test_derive_claude_manifest_cap_raise() -> None:
    """D3: cap enforcement — raises RuntimeError when > 24 domains."""
    import tempfile

    import yaml  # type: ignore[import]

    # Floor domains must be present to bypass floor assertion and hit cap check.
    floor_domains = ["cortex", "agent_bus", "fs", "dispatch"]
    extra_domains = [f"domain_{i}" for i in range(25 - len(floor_domains))]
    all_domains = floor_domains + extra_domains
    fake_tools = [
        {
            "canonical_name": f"{d}_op",
            "domain": d,
            "dispatcher_call_shape": {
                "tool": d,
                "dispatch_key": "op",
                "dispatch_value": "test",
            },
            "flat_call_shape": {"tool": f"{d}_op"},
            "seat_visibility": ["mcp_claude"],
        }
        for d in all_domains
    ]
    data = {"schema_version": 1, "tools": fake_tools}
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        tmp = Path(f.name)
    with pytest.raises(RuntimeError, match="Claude manifest cap exceeded"):
        derive_claude_manifest(tmp)


def test_derive_claude_manifest_mcp_claude_visibility_only() -> None:
    """D2: every entry in Claude manifest has at least one tool with mcp_claude in seat_visibility."""
    import yaml  # type: ignore[import]

    raw = yaml.safe_load(_CANONICAL_YAML.read_text(encoding="utf-8"))
    tools_by_domain: dict[str, list[list[str]]] = {}
    for t in raw.get("tools", []):
        tools_by_domain.setdefault(t["domain"], []).append(t.get("seat_visibility", []))
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    for entry in manifest:
        domain_visibilities = tools_by_domain.get(entry["domain"], [])
        assert any("mcp_claude" in sv for sv in domain_visibilities), (
            f"domain {entry['domain']!r} in Claude manifest but no tool has mcp_claude"
        )


def test_derive_claude_manifest_domains_sorted() -> None:
    """D4: entries sorted by domain name for byte stability."""
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    domains = [e["domain"] for e in manifest]
    assert domains == sorted(domains), "Manifest entries not sorted by domain"


def test_derive_claude_manifest_ops_sorted() -> None:
    """D4: ops list within each entry is sorted."""
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    for entry in manifest:
        assert entry["ops"] == sorted(entry["ops"]), (
            f"ops for domain {entry['domain']!r} not sorted: {entry['ops']}"
        )


def test_derive_claude_manifest_op_skills_recovers_divergent_bindings() -> None:
    """op_skills surfaces per-op skill_uri lost in domain grouping.

    cortex.session_close diverges (agent_skill:session-close) from the cortex
    domain binding (agent_skill:cortex); preflight diverges to
    agent_skill:session-close-audit. Both must appear under op_skills; ops whose
    binding equals the domain's must NOT (no information lost ⟹ no duplication).
    """
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    cortex = next(e for e in manifest if e["domain"] == "cortex")
    op_skills = cortex.get("op_skills", {})

    assert op_skills.get("session_close") == "rule:session-close"
    assert op_skills.get("session_close_preflight") == "rule:session-close-audit"
    assert op_skills.get("implement_ready_preflight") == "rule:implement-todo"
    # Non-divergent cortex ops (skill_uri == domain binding) are not duplicated.
    assert "search" not in op_skills
    assert op_skills["session_close"] != cortex["skill_uri"]
    # Byte-stability: op_skills keys are sorted.
    assert list(op_skills.keys()) == sorted(op_skills.keys())


def test_derive_claude_manifest_op_skills_absent_when_no_divergence() -> None:
    """Domains whose ops all share the domain binding carry no op_skills key."""
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    fs_entry = next(e for e in manifest if e["domain"] == "fs")
    assert "op_skills" not in fs_entry


def test_team_dispatch_json_schema_includes_server_tools() -> None:
    """Both team_dispatch generate/to_thread entries expose server_tools."""
    import yaml

    data = yaml.safe_load(_CANONICAL_YAML.read_text(encoding="utf-8"))
    for name in ("team_dispatch_generate", "team_dispatch_to_thread"):
        entry = next(t for t in data["tools"] if t["canonical_name"] == name)
        props = entry["json_schema"]["properties"]
        assert props.get("server_tools") == {"type": "boolean"}, name
