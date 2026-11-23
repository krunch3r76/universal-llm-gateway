"""Nested heading tree for md_to_dict using lean row metadata (no bodies)."""

from __future__ import annotations

from typing import Any

from markdown_sections import PREAMBLE_ID, _document_sections

from tools._document_manifest import (
    UNSECTIONED_ID,
    _assign_section_ids,
    _indexed_by_line,
    _is_xml_nav_section,
)
from tools._lean_document_manifest import (
    _build_lean_rows,
    _preamble_from_nav,
    _validate_max_depth,
)


def _effective_to_dict_max_depth(max_depth: int) -> int:
    """md_list default is 3; md_to_dict uses full playbook depth unless overridden."""
    if max_depth == 3:
        return 6
    return max_depth


def _heading_tree_key(sec: Any) -> str:
    if _is_xml_nav_section(sec):
        return sec.path
    return sec.heading


def lean_heading_tree_dict(
    text: str, *, max_depth: int = 6
) -> dict[str, Any]:
    """Title-nested dict with lean row fields per node; no section body strings."""
    depth, depth_err = _validate_max_depth(max_depth)
    if depth_err is not None:
        return depth_err

    nav = _document_sections(text)
    if not any(s.level > 0 for s in nav):
        return {
            UNSECTIONED_ID: {
                "id": UNSECTIONED_ID,
                "level": 0,
                "char_count": len(text),
                "subtree_char_count": len(text),
            }
        }

    rows, _depth_cut = _build_lean_rows(text, nav, max_depth=depth)
    row_by_id = {row["id"]: dict(row) for row in rows}

    trimmed = [s for s in nav if not (s.level == 0 and s.chars == 0)]
    atx_nav = [s for s in trimmed if s.level > 0 and not _is_xml_nav_section(s)]
    indexed = _assign_section_ids(atx_nav)
    by_line = _indexed_by_line(indexed)

    root: dict[str, Any] = {}
    preamble_len, _line = _preamble_from_nav(nav)
    if preamble_len > 0 and PREAMBLE_ID in row_by_id:
        root["_preamble"] = dict(row_by_id[PREAMBLE_ID])

    stack: list[tuple[int, dict[str, Any]]] = []
    for sec in trimmed:
        if sec.level == 0:
            continue
        if sec.level > depth:
            continue
        if _is_xml_nav_section(sec):
            row_id = sec.path
        else:
            row = by_line.get(sec.line)
            if row is None:
                continue
            row_id = row.section_id
        if row_id not in row_by_id:
            continue
        node = dict(row_by_id[row_id])
        while stack and stack[-1][0] >= sec.level:
            stack.pop()
        parent = stack[-1][1] if stack else root
        parent[_heading_tree_key(sec)] = node
        stack.append((sec.level, node))

    return root


__all__ = ["lean_heading_tree_dict", "_effective_to_dict_max_depth"]
