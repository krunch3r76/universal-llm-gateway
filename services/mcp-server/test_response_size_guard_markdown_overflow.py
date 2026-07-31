"""Tests for markdown-shaped overflow replacements in the response size guard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp.tools.tool import ToolResult
from markdown_sections import list_sections
from request_profile import bind_profile
from response_overflow_manifest import (
    STRUCTURE_BUDGET_BYTES,
    STRUCTURE_MAX_SECTIONS,
    _cap_sections,
    build_markdown_overflow_manifest,
)
from response_size_guard import (
    _CURSOR_THRESHOLD,
    _DEFAULT_THRESHOLD,
    _agent_bus_manifest,
    _cortex_manifest,
    _measure_result,
    _replacement_result,
    _store,
    _store_result,
)


def _clear_store() -> None:
    _store.clear()


def _nested_markdown(*, body_repeat: int = 8_000) -> str:
    """Synthetic markdown with nested headings and a large body."""
    filler = "x" * body_repeat
    return (
        "# Root\n\n"
        "intro\n\n"
        "## Results\n\n"
        f"{filler}\n\n"
        "### Query: alpha\n\n"
        "query body alpha\n\n"
        "### Query: beta\n\n"
        "query body beta\n\n"
        "## Appendix\n\n"
        "tail\n"
    )


def _fs_overflow_result(
    text: str,
    *,
    uri: str = "cortex://notes/system/recon/example.md",
    path: str = "notes/system/recon/example.md",
) -> ToolResult:
    return ToolResult(
        structured_content={
            "content": text,
            "uri": uri,
            "path": path,
            "size": len(text.encode("utf-8")),
        }
    )


def _guard_replacement(
    result: ToolResult,
    *,
    tool_name: str = "fs",
    threshold: int,
    profile: str = "default",
) -> ToolResult:
    _clear_store()
    size = _measure_result(result)
    with bind_profile(profile):
        ref_id = _store_result(tool_name, result, size)
        return _replacement_result(ref_id, tool_name, size, threshold, result)


def test_markdown_overflow_includes_sections_and_ref_id() -> None:
    text = _nested_markdown(body_repeat=120_000)
    original = _fs_overflow_result(text)
    replacement = _guard_replacement(
        original, threshold=_DEFAULT_THRESHOLD, profile="default"
    )

    manifest = replacement.structured_content
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "markdown_structure"
    assert manifest["ref_id"].startswith("rs_")
    assert manifest["large_payload"] is True
    assert "sections" in manifest

    expected = list_sections(text)
    emitted = manifest["sections"]
    assert len(emitted) == len(expected)
    for row in emitted:
        assert set(row) == {"heading", "level", "path", "line", "chars"}
    assert any(row["level"] == 3 for row in emitted)


def test_retrieve_returns_original_markdown_payload() -> None:
    text = _nested_markdown(body_repeat=120_000)
    original = _fs_overflow_result(text)
    _clear_store()
    size = _measure_result(original)
    ref_id = _store_result("fs", original, size)
    replacement = _replacement_result(ref_id, "fs", size, _DEFAULT_THRESHOLD, original)
    manifest = replacement.structured_content
    assert isinstance(manifest, dict)
    stored_ref = manifest["ref_id"]
    assert stored_ref in _store

    stored = _store.pop(stored_ref)
    assert stored is not None
    restored = stored.result
    assert restored.structured_content["content"] == text


def test_structure_cap_sets_truncated_marker() -> None:
    rows = [
        {
            "heading": f"H{i}",
            "level": 1 if i % 2 == 0 else 3,
            "path": f"H{i}",
            "line": i + 1,
            "chars": 500,
        }
        for i in range(250)
    ]
    capped, truncated = _cap_sections(
        rows,
        max_sections=STRUCTURE_MAX_SECTIONS,
        max_bytes=STRUCTURE_BUDGET_BYTES,
    )
    assert truncated is True
    assert len(capped) <= STRUCTURE_MAX_SECTIONS
    assert (
        len(json.dumps(capped, ensure_ascii=False).encode("utf-8"))
        <= STRUCTURE_BUDGET_BYTES
    )
    assert all(int(row["level"]) <= 2 for row in capped[:10])


def test_replacement_under_cursor_safe_and_default_thresholds() -> None:
    text = _nested_markdown(body_repeat=120_000)
    original = _fs_overflow_result(text)

    for profile, threshold in (
        ("cursor_safe", _CURSOR_THRESHOLD),
        ("default", _DEFAULT_THRESHOLD),
    ):
        replacement = _guard_replacement(
            original, threshold=threshold, profile=profile
        )
        assert _measure_result(replacement) <= threshold


def _tool_text(result: ToolResult) -> str:
    content = result.content
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            return text
    return ""


def test_non_markdown_oversized_payload_stays_opaque() -> None:
    blob = json.dumps({"items": [{"id": i, "value": "y" * 200} for i in range(900)]})
    original = ToolResult(structured_content={"content": blob, "path": "data.json"})
    replacement = _guard_replacement(original, threshold=_DEFAULT_THRESHOLD)

    assert replacement.structured_content is None
    note = _tool_text(replacement)
    assert "sections" not in note
    assert "retrieve(id=" in note
    assert "Section tree" not in note


def test_agent_bus_manifest_unchanged() -> None:
    payload = {
        "turns": [
            {
                "thread": "1138",
                "turn_number": 1,
                "subject": "sample",
                "body": "# Title\n\nbody",
            }
        ]
    }
    manifest = _agent_bus_manifest("rs_abcd01", payload, size=256_000, threshold=128_000)
    assert manifest["tool"] == "agent_bus"
    assert manifest["kind"] == "turns"
    assert "sections" not in manifest
    assert manifest["turn_samples"][0]["markdown_sections"] == ["Title"]


def test_cortex_manifest_unchanged() -> None:
    payload = {
        "id": "note:example",
        "type": "note",
        "name": "Example",
        "assertions": [{"id": 1, "claim": "x" * 40}],
    }
    manifest = _cortex_manifest("rs_cdef01", payload, size=256_000, threshold=128_000)
    assert manifest["tool"] == "cortex"
    assert manifest["kind"] == "entity_get"
    assert "sections" not in manifest
    assert manifest["entity"]["id"] == "note:example"


def test_sections_match_list_sections_modulo_truncation() -> None:
    sections = []
    for i in range(220):
        sections.append(f"## Section {i}\n\ncontent {i}\n")
    text = "# Doc\n\n" + "".join(sections) + ("z" * 100_000)
    original = _fs_overflow_result(text)
    replacement = _guard_replacement(original, threshold=_DEFAULT_THRESHOLD)
    manifest = replacement.structured_content
    assert isinstance(manifest, dict)
    assert manifest["structure_truncated"] is True

    full_rows = list_sections(text)
    emitted = manifest["sections"]
    assert len(emitted) < len(full_rows)
    assert emitted == full_rows[: len(emitted)]
    for row in emitted:
        assert set(row) == {"heading", "level", "path", "line", "chars"}


def test_manifest_builder_returns_none_for_plain_text() -> None:
    original = ToolResult(structured_content={"content": "plain text without headings"})
    manifest = build_markdown_overflow_manifest(
        "rs_plain1", "fs", 200_000, _DEFAULT_THRESHOLD, original
    )
    assert manifest is None
