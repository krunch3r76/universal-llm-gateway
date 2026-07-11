"""Markdown-shaped overflow manifests for the response size guard.

Builds md_list-equivalent section trees for oversized markdown tool results
while keeping full payloads in the rs_ store for retrieve pop semantics.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastmcp.tools.tool import ToolResult
from markdown_sections import SectionError
from markdown_sections import list_sections as md_list_sections

STRUCTURE_BUDGET_BYTES: int = 16 * 1024
STRUCTURE_MAX_SECTIONS: int = 200

_SECTION_KEYS: frozenset[str] = frozenset(
    {"heading", "level", "path", "line", "chars"}
)


def _walk_strings(obj: Any) -> list[str]:
    """Recursively collect UTF-8 text leaves from a JSON-serializable object."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out: list[str] = []
        for key, value in obj.items():
            if key == "content_base64":
                continue
            out.extend(_walk_strings(value))
        return out
    if isinstance(obj, list):
        out: list[str] = []
        for value in obj:
            out.extend(_walk_strings(value))
        return out
    return []


def _extract_primary_text(result: ToolResult, tool_name: str) -> str | None:
    """Return the best markdown candidate string from a ToolResult, if any."""
    structured = result.structured_content
    if isinstance(structured, dict):
        content = structured.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if structured.get("content_base64"):
            return None

    candidates = _walk_strings(structured) if isinstance(structured, dict) else []
    for item in result.content if isinstance(result.content, list) else []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            candidates.append(text)
    if isinstance(result.content, str):
        candidates.append(result.content)

    if not candidates:
        return None
    if tool_name == "fs" and isinstance(structured, dict):
        fs_content = structured.get("content")
        if isinstance(fs_content, str) and fs_content.strip():
            return fs_content
    return max(candidates, key=len)


def _is_markdown_shaped(text: str) -> bool:
    """True when list_sections finds at least one ATX heading row."""
    if not text.strip():
        return False
    try:
        sections = md_list_sections(text)
    except SectionError:
        return False
    return any(int(section.get("level", 0)) >= 1 for section in sections)


def _section_row_bytes(rows: list[dict[str, Any]]) -> int:
    return len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))


def _normalize_section_rows(rows: list[dict[str, str | int]]) -> list[dict[str, Any]]:
    """Keep only md_list keys with stable JSON-serializable values."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "heading": str(row.get("heading", "")),
                "level": int(row.get("level", 0)),
                "path": str(row.get("path", "")),
                "line": int(row.get("line", 0)),
                "chars": int(row.get("chars", 0)),
            }
        )
    return normalized


def _cap_sections(
    rows: list[dict[str, Any]],
    *,
    max_sections: int,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Cap section rows by count and serialized byte budget."""
    if not rows:
        return [], False

    def can_add(selected: list[dict[str, Any]], row: dict[str, Any]) -> bool:
        candidate = selected + [row]
        if len(candidate) > max_sections:
            return False
        return _section_row_bytes(candidate) <= max_bytes

    le2 = [row for row in rows if int(row.get("level", 99)) <= 2]
    deeper = [row for row in rows if int(row.get("level", 0)) > 2]

    selected: list[dict[str, Any]] = []
    for row in le2:
        if can_add(selected, row):
            selected.append(row)
        else:
            return selected, True

    for row in deeper:
        if can_add(selected, row):
            selected.append(row)
        else:
            return selected, True

    return selected, len(selected) < len(rows)


def _resolve_durable_location(
    result: ToolResult,
) -> tuple[str | None, str | None]:
    """Return (uri, path) when the overflowing result names a durable location."""
    structured = result.structured_content
    if not isinstance(structured, dict):
        return None, None
    uri = structured.get("uri")
    path = structured.get("path")
    uri_s = str(uri).strip() if isinstance(uri, str) and uri.strip() else None
    path_s = str(path).strip() if isinstance(path, str) and path.strip() else None
    return uri_s, path_s


def _build_selective_options(
    *,
    ref_id: str,
    uri: str | None,
    path: str | None,
    sections: list[dict[str, Any]],
) -> list[str]:
    """Build selective follow-up hints for markdown overflow replacements."""
    location = uri or path
    options: list[str] = []
    if location:
        options.append(f'fs(op="md_list", path="{location}")')
        if sections:
            section_path = str(sections[0].get("path", "")).strip()
            if section_path:
                options.append(
                    f'fs(op="md_read", path="{location}", section="{section_path}")'
                )
        options.append(f'fs(op="read", path="{location}", offset=0, limit=80)')
    options.append(f'retrieve(id="{ref_id}")')
    return options


def build_markdown_overflow_manifest(
    ref_id: str,
    tool_name: str,
    size: int,
    threshold: int,
    result: ToolResult,
) -> dict[str, Any] | None:
    """Return a markdown structure manifest, or None when not eligible."""
    text = _extract_primary_text(result, tool_name)
    if text is None or not _is_markdown_shaped(text):
        return None

    try:
        raw_sections = md_list_sections(text)
    except SectionError:
        return None

    rows = _normalize_section_rows(raw_sections)
    capped_rows, truncated = _cap_sections(
        rows,
        max_sections=STRUCTURE_MAX_SECTIONS,
        max_bytes=STRUCTURE_BUDGET_BYTES,
    )
    uri, path = _resolve_durable_location(result)
    selective_options = _build_selective_options(
        ref_id=ref_id,
        uri=uri,
        path=path,
        sections=capped_rows,
    )

    manifest: dict[str, Any] = {
        "large_payload": True,
        "tool": tool_name,
        "kind": "markdown_structure",
        "size_bytes": size,
        "size_kb": round(size / 1024, 1),
        "threshold_bytes": threshold,
        "threshold_kb": round(threshold / 1024, 1),
        "ref_id": ref_id,
        "prefer_selective_reads": True,
        "full_retrieve_last_resort": f'retrieve(id="{ref_id}")',
        "structure_truncated": truncated,
        "structure_rows": len(capped_rows),
        "sections": capped_rows,
        "selective_options": selective_options,
    }
    if path:
        manifest["path"] = path
    if uri:
        manifest["uri"] = uri
    return manifest


def format_markdown_overflow_note(manifest: dict[str, Any]) -> str:
    """Render the natural-language overflow note for markdown structure manifests."""
    ref_id = str(manifest["ref_id"])
    truncated = bool(manifest.get("structure_truncated"))
    structure_rows = int(manifest.get("structure_rows", 0))
    uri = manifest.get("uri")
    path = manifest.get("path")
    location = uri or path

    tree_suffix = ""
    if truncated:
        tree_suffix = (
            "; truncated — use md_list for full outline"
            if location
            else "; truncated"
        )

    note_lines = [
        "Large markdown payload flagged.",
        (
            f"Size: {manifest['size_kb']}KB over "
            f"{manifest['threshold_kb']}KB threshold."
        ),
        f"Stored as: {ref_id} (expires in 10 min).",
        f"Section tree: {structure_rows} rows{tree_suffix}.",
        "",
        "Prefer selective follow-ups before full retrieval:",
    ]
    for option in manifest.get("selective_options", []):
        if option != f'retrieve(id="{ref_id}")':
            note_lines.append(f"- {option}")
    note_lines.extend(
        [
            "",
            "Use full retrieval only as a last resort:",
            f'- retrieve(id="{ref_id}")',
        ]
    )
    return "\n".join(note_lines)


def try_markdown_overflow_replacement(
    ref_id: str,
    tool_name: str,
    size: int,
    threshold: int,
    result: ToolResult,
    *,
    measure_result: Callable[[ToolResult], int],
) -> ToolResult | None:
    """Build a markdown structure replacement when it fits under the active threshold."""
    manifest = build_markdown_overflow_manifest(
        ref_id, tool_name, size, threshold, result
    )
    if manifest is None:
        return None

    replacement = ToolResult(
        content=format_markdown_overflow_note(manifest),
        structured_content=manifest,
    )
    if measure_result(replacement) > threshold:
        return None
    return replacement
