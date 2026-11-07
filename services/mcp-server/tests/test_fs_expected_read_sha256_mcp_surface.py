"""Served fs tool must accept a per-path expected_read_sha256 map.

Direct fs_impl tests already pass a map. FastMCP builds the fs schema from
the server wrapper annotation, so a string-only annotation rejects the map
before the handler.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

pytestmark = pytest.mark.offline


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(root: Path, rel: str, text: str) -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return _sha(target)


def _bind_workspaces(monkeypatch: pytest.MonkeyPatch, projects: Path) -> None:
    def _root(surface: str, root_id: str) -> Path:
        assert root_id == "workspaces"
        return projects

    monkeypatch.setattr("fs_roots.fs_root_for", _root)


def _mcp():
    from server import _build_server

    return _build_server("code")[0]


def _hash_schema(mcp: Any) -> dict[str, Any]:
    getter = mcp.get_tool
    tool = getter("fs")
    if asyncio.iscoroutine(tool):
        tool = asyncio.run(tool)
    params = tool.parameters
    props = params["properties"] if isinstance(params, dict) else params.properties
    schema = props["expected_read_sha256"]
    if not isinstance(schema, dict):
        schema = dict(schema)
    return schema


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple):
        for item in result:
            if isinstance(item, dict):
                return item
        for item in result:
            return _as_dict(item)
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    if data is not None and data is not result:
        return _as_dict(data)
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    if content and hasattr(content[0], "text"):
        loaded = json.loads(content[0].text)
        if isinstance(loaded, dict):
            return loaded
    raise AssertionError(repr(result)[:800])


def _call(mcp: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(asyncio.run(mcp.call_tool("fs", arguments)))


def test_generated_schema_admits_string_or_map() -> None:
    schema = _hash_schema(_mcp())
    jsonschema.Draft202012Validator(schema).validate("")
    jsonschema.Draft202012Validator(schema).validate({"notes/a.md": "ab"})
    assert schema.get("type") != "string" or "anyOf" in schema or "oneOf" in schema


def test_call_tool_per_path_map_splits_stale_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projects = tmp_path / "projects"
    bad = "universal-llm-gateway/notes/bad.md"
    good = "universal-llm-gateway/notes/good.md"
    _write(projects, bad, "# Bad\n\nstale body\n")
    good_live = _write(projects, good, "# Good\n\nmatched body\n")
    _bind_workspaces(monkeypatch, projects)
    wrong = "0" * 64
    payload = _call(
        _mcp(),
        {
            "op": "read_multi",
            "sandbox": "workspaces",
            "paths": [bad, good],
            "expected_read_sha256": {bad: wrong, good: good_live},
        },
    )
    assert payload.get("selection") == "multi", payload
    assert payload.get("read_multi") is True
    stale = payload["files"][bad]
    matched = payload["files"][good]
    assert stale.get("outcome") == "stale_handle", stale
    assert stale.get("selection") == "manifest"
    assert stale.get("expected_read_sha256") == wrong
    assert "content" not in stale
    assert "sections" not in stale
    assert "descriptors" not in stale
    assert matched.get("selection") == "manifest", matched
    assert isinstance(matched.get("sections"), list) and matched["sections"]
    assert "content" not in matched
    assert matched.get("outcome") != "stale_handle"


def test_scalar_still_accepts_one_document_and_rejects_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projects = tmp_path / "projects"
    one = "universal-llm-gateway/notes/one.md"
    two = "universal-llm-gateway/notes/two.md"
    live = _write(projects, one, "# One\n\nbody\n")
    _write(projects, two, "# Two\n\nbody\n")
    _bind_workspaces(monkeypatch, projects)
    mcp = _mcp()
    single = _call(
        mcp,
        {
            "op": "read_multi",
            "sandbox": "workspaces",
            "paths": [one],
            "expected_read_sha256": live,
        },
    )
    assert single.get("code") != "invalid_selector_combo", single
    entry = single["files"][one]
    assert entry.get("selection") == "manifest", entry
    assert isinstance(entry.get("sections"), list) and entry["sections"]
    conflict = _call(
        mcp,
        {
            "op": "read_multi",
            "sandbox": "workspaces",
            "paths": [one, two],
            "expected_read_sha256": live,
        },
    )
    assert conflict.get("code") == "invalid_selector_combo", conflict
    assert "scalar" in conflict.get("message", "")
