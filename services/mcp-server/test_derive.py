"""Snapshot test for derive_grok_manifest (G2 / P11) and derive_claude_manifest (Phase D).

∀ run: derive_grok_manifest(canonical_yaml_path) ⟹ identical JSON output.

Golden files:
  testdata/golden_grok_manifest.json
  testdata/golden_claude_manifest.json
Refresh golden:
  python3 -m pytest services/mcp-server/test_derive.py --refresh-golden
  (or run: python3 services/mcp-server/test_derive.py --refresh)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _derive import derive_claude_manifest, derive_grok_manifest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CANONICAL_YAML = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"
_GOLDEN = Path(__file__).parent / "testdata" / "golden_grok_manifest.json"
_TESTDATA = Path(__file__).parent / "testdata"


def _serialise(manifest: list) -> str:
    return json.dumps(manifest, sort_keys=True, indent=2)


def test_derive_grok_manifest_matches_golden() -> None:
    """G2: derived manifest must match committed golden file byte-exactly."""
    assert _CANONICAL_YAML.exists(), f"canonical.yaml missing: {_CANONICAL_YAML}"
    assert _GOLDEN.exists(), (
        f"Golden file missing: {_GOLDEN}\n"
        "Refresh: python3 services/mcp-server/test_derive.py --refresh"
    )

    manifest = derive_grok_manifest(_CANONICAL_YAML)
    assert manifest, "derive_grok_manifest returned empty list"

    actual = _serialise(manifest)
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert actual == expected, (
        "G2 snapshot drift — canonical.yaml changed without updating golden.\n"
        "If the change is intentional, refresh:\n"
        "  python3 services/mcp-server/test_derive.py --refresh"
    )


def test_derive_is_deterministic() -> None:
    """G2: two sequential calls produce byte-identical output."""
    m1 = derive_grok_manifest(_CANONICAL_YAML)
    m2 = derive_grok_manifest(_CANONICAL_YAML)
    assert _serialise(m1) == _serialise(m2)


def test_derive_all_have_required_fields() -> None:
    """G2: every manifest entry has the required fields."""
    manifest = derive_grok_manifest(_CANONICAL_YAML)
    required = {"canonical_name", "name", "description", "inputSchema", "domain"}
    for entry in manifest:
        missing = required - set(entry.keys())
        assert not missing, (
            f"Entry {entry.get('canonical_name')!r} missing fields: {missing}"
        )


def test_derive_entries_sorted_by_canonical_name() -> None:
    """G2: entries are sorted by canonical_name for byte stability."""
    manifest = derive_grok_manifest(_CANONICAL_YAML)
    names = [e["canonical_name"] for e in manifest]
    assert names == sorted(names), "Manifest entries not sorted by canonical_name"


def test_derive_mcp_grok_visibility_only() -> None:
    """G2: every entry in the manifest has mcp_grok in seat_visibility."""
    import yaml  # type: ignore[import]

    raw = yaml.safe_load(_CANONICAL_YAML.read_text(encoding="utf-8"))
    all_tools = {t["canonical_name"]: t for t in raw.get("tools", [])}
    manifest = derive_grok_manifest(_CANONICAL_YAML)
    for entry in manifest:
        cn = entry["canonical_name"]
        assert cn in all_tools, f"{cn} in manifest but not in canonical.yaml"
        sv = all_tools[cn].get("seat_visibility", [])
        assert "mcp_grok" in sv, f"{cn} in grok manifest but seat_visibility={sv!r}"


# ── Phase D: derive_claude_manifest tests ─────────────────────────────────────


def test_derive_claude_manifest_count() -> None:
    """D1: derived Claude manifest returns exactly one entry per domain."""
    manifest = derive_claude_manifest(_CANONICAL_YAML)
    assert len(manifest) == 14  # update if domains change
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
            "seat_visibility": ["mcp_claude", "mcp_grok"],
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


def test_grok_golden_unchanged_after_claude_registry_diff() -> None:
    """Regression: Claude registry diff must not perturb grok manifest bytes."""
    grok_manifest = derive_grok_manifest(_CANONICAL_YAML)
    golden = json.loads((_TESTDATA / "golden_grok_manifest.json").read_text())
    assert grok_manifest == golden, "grok manifest drifted — Phase D broke grok partition"


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        from _derive import derive_grok_manifest as _d

        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(_serialise(_d(_CANONICAL_YAML)))
        print(f"Golden refreshed: {_GOLDEN} ({_GOLDEN.stat().st_size} bytes)")
    else:
        print("Usage: python3 test_derive.py --refresh")
        sys.exit(1)
