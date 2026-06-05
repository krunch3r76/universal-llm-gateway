"""Markdown section MCP tool: section-level navigation/edits via markdown_sections."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_sections import (
    SectionError,
    dict_from_json,
    dict_to_markdown,
    sections_to_dict,
)
from markdown_sections import (
    append_section as md_append_section,
)
from markdown_sections import (
    delete_section as md_delete_section,
)
from markdown_sections import (
    list_sections as md_list_sections,
)
from markdown_sections import (
    read_section as md_read_section,
)
from markdown_sections import (
    replace_section as md_replace_section,
)
from mcp_events import record

from ._file_helpers import extract_text_content, is_converted_format
from ._write_policy import project_write_denied_error, project_writes_enabled

if TYPE_CHECKING:
    from fastmcp import FastMCP

_FILES_ROOT = Path("/data/files")
_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))


def _resolve_sandbox(sandbox: str, path: str) -> Path:
    if sandbox == "cortex":
        root = _FILES_ROOT.resolve()
    elif sandbox == "workspaces":
        root = _PROJECT_ROOT.resolve()
    else:
        raise ValueError(
            f"Unknown sandbox {sandbox!r}. Use 'cortex' (/data/files) "
            "or 'workspaces' (project root)"
        )
    candidate = (root / path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        record("mcp.tool.path.traversal.rejected", path=path, sandbox=sandbox)
        raise ValueError(
            f"Path {path!r} resolves outside {sandbox} root; traversal rejected"
        ) from None
    return candidate


def _load_text(resolved: Path) -> tuple[str | None, str | None]:
    """Load text content, using format-specific extraction for PDF/DOCX/etc."""
    try:
        if not resolved.exists():
            return None, f"File not found: {resolved.name}"
        if not resolved.is_file():
            return None, f"Not a file: {resolved.name}"
        return extract_text_content(resolved), None
    except Exception as e:
        return None, str(e)


def _write_file(resolved: Path, text: str) -> None:
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")


def _mutate_document(resolved: Path, transform: Callable[[str], str]) -> str | None:
    if is_converted_format(resolved):
        return (
            f"Cannot modify {resolved.suffix} files via section ops — "
            "converted formats are read-only (use md_list / md_read)"
        )
    if not project_writes_enabled():
        return project_write_denied_error()["error"]
    text, err = _load_text(resolved)
    if err:
        return err
    try:
        updated = transform(text)
    except SectionError as e:
        return str(e)
    try:
        _write_file(resolved, updated)
    except OSError as e:
        return project_write_denied_error()["error"] + f" (OS: {e})"
    return None


def _section_write_result(
    resolved: Path,
    path: str,
    sandbox: str,
    section: str,
    signal: str,
    status: str,
    transform: Callable[[str], str],
) -> dict[str, Any]:
    err = _mutate_document(resolved, transform)
    if err:
        return {"error": err}
    record(signal, path=path, sandbox=sandbox, section=section)
    return {"status": status, "path": path, "section": section}


def register_markdown_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Markdown Operations")
    def markdown(
        op: str,
        path: str,
        sandbox: str = "context",
        section: str = "",
        content: str = "",
    ) -> dict[str, Any]:
        """Section-level markdown: list/read/replace/append/delete/to_dict/from_dict.

        Sandboxes: context → tasks/; cortex → /data/files; workspaces → project root.
        Section path from list_sections; "" = preamble. from_dict: content is JSON.
        Prefer over whole-file context/cortex/workspaces for long structured docs.
        Use workspaces sandbox for tmp/ files (debrief log, phase docs, handoff docs).

        PDF, DOCX, ODT, EML, and HTML files are auto-converted to markdown for
        read ops (list_sections, read_section, to_dict). Write ops
        (replace/append/delete/from_dict) are text-only — converted formats
        are rejected.
        """
        if not op:
            return {"error": "'op' is required"}
        if not path:
            return {"error": "'path' is required"}
        try:
            resolved = _resolve_sandbox(sandbox, path)
        except ValueError as e:
            return {"error": str(e)}

        if op in ("list_sections", "read_section", "to_dict"):
            text, err = _load_text(resolved)
            if err:
                return {"error": err}
            if op == "list_sections":
                try:
                    sections = md_list_sections(text)
                except SectionError as e:
                    return {"error": str(e)}
                record(
                    "mcp.tool.markdown.sections.listed",
                    path=path,
                    sandbox=sandbox,
                    count=len(sections),
                )
                return {
                    "path": path,
                    "sandbox": sandbox,
                    "total_chars": len(text),
                    "sections": sections,
                }
            if op == "read_section":
                try:
                    body = md_read_section(text, section)
                except SectionError as e:
                    return {"error": str(e)}
                record(
                    "mcp.tool.markdown.section.read",
                    path=path,
                    sandbox=sandbox,
                    section=section,
                    chars=len(body),
                )
                return {
                    "content": body,
                    "path": path,
                    "sandbox": sandbox,
                    "section": section,
                }
            try:
                data = sections_to_dict(text)
            except SectionError as e:
                return {"error": str(e)}
            record(
                "mcp.tool.markdown.converted.to.dict",
                path=path,
                sandbox=sandbox,
                keys=len(data),
            )
            return {"data": data, "path": path, "sandbox": sandbox}

        if op == "replace_section":
            return _section_write_result(
                resolved,
                path,
                sandbox,
                section,
                "mcp.tool.markdown.section.replaced",
                "replaced",
                lambda t: md_replace_section(t, section, content),
            )
        if op == "append_section":
            if not content:
                return {"error": "'content' is required for append_section"}
            return _section_write_result(
                resolved,
                path,
                sandbox,
                section,
                "mcp.tool.markdown.section.appended",
                "appended",
                lambda t: md_append_section(t, section, content),
            )
        if op == "delete_section":
            return _section_write_result(
                resolved,
                path,
                sandbox,
                section,
                "mcp.tool.markdown.section.deleted",
                "deleted",
                lambda t: md_delete_section(t, section),
            )
        if op == "from_dict":
            if is_converted_format(resolved):
                return {
                    "error": (
                        f"Cannot write to {resolved.suffix} files — "
                        "converted formats are read-only"
                    )
                }
            if not project_writes_enabled():
                return project_write_denied_error()
            if not content:
                return {"error": "'content' (JSON) is required for from_dict"}
            try:
                data = dict_from_json(content)
                md_text = dict_to_markdown(data)
                _write_file(resolved, md_text)
            except (SectionError, OSError) as e:
                return {"error": str(e)}
            record(
                "mcp.tool.markdown.converted.from.dict",
                path=path,
                sandbox=sandbox,
                keys=len(data),
            )
            return {"status": "written", "path": path, "chars": len(md_text)}

        return {
            "error": (
                f"Unknown op: {op!r}. Use: list_sections, read_section, "
                "replace_section, append_section, delete_section, to_dict, from_dict"
            )
        }
