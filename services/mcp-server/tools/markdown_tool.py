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
    insert_section as md_insert_section,
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
from ._pdf_sections import (
    PdfSectionError,
    list_pdf_sections,
    pdf_to_dict,
    read_pdf_section,
)
from ._section_mutation import (
    delete_mutation_summary,
    delete_warning,
    section_mutation_summary,
    shrink_warning,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

_FILES_ROOT = Path("/data/files")
_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))


def _resolve_sandbox(sandbox: str, path: str) -> Path:
    from implement_admission.scheme_resolve import resolve_fs_ingress

    try:
        ingress = resolve_fs_ingress(path, sandbox=sandbox)
        if ingress.resolved is not None:
            return ingress.resolved
        rel = ingress.rel_path.lstrip("/")
    except ValueError:
        rel = path.lstrip("/")
    if sandbox == "cortex":
        root = _FILES_ROOT.resolve()
    elif sandbox == "workspaces":
        root = _PROJECT_ROOT.resolve()
    else:
        raise ValueError(
            f"Unknown sandbox {sandbox!r}. Use 'cortex' or 'workspaces' "
            "with Share URI paths (cortex:// / workspaces://)."
        )
    candidate = (root / rel).resolve()
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


def _mutate_document(
    resolved: Path, transform: Callable[[str], str | tuple[str, bool]]
) -> tuple[str | None, bool]:
    if is_converted_format(resolved):
        return (
            f"Cannot modify {resolved.suffix} files via section ops — "
            "converted formats are read-only (use md_list / md_read)",
            False,
        )
    text, err = _load_text(resolved)
    if err:
        return err, False
    try:
        result = transform(text)
    except SectionError as e:
        return str(e), False
    if isinstance(result, tuple):
        updated, normalized = result
    else:
        updated, normalized = result, False
    try:
        _write_file(resolved, updated)
    except OSError as e:
        return f"Failed to write {resolved}: {e}", False
    return None, normalized


def _section_write_result(
    resolved: Path,
    path: str,
    sandbox: str,
    section: str,
    signal: str,
    status: str,
    transform: Callable[[str], tuple[str, bool]],
    *,
    mutation_op: str | None = None,
    warn_on_shrink: bool = False,
) -> dict[str, Any]:
    if is_converted_format(resolved):
        return {
            "error": (
                f"Cannot modify {resolved.suffix} files via section ops — "
                "converted formats are read-only (use md_list / md_read)"
            )
        }
    text, err = _load_text(resolved)
    if err:
        return {"error": err}
    try:
        prior_body = md_read_section(text, section)
    except SectionError as exc:
        return {"error": str(exc)}
    try:
        raw = transform(text)
    except SectionError as exc:
        return {"error": str(exc)}
    if isinstance(raw, tuple):
        updated, normalized = raw
    else:
        updated, normalized = raw, False
    try:
        new_body = md_read_section(updated, section)
    except SectionError as exc:
        return {"error": str(exc)}
    try:
        _write_file(resolved, updated)
    except OSError as exc:
        return {"error": f"Failed to write {resolved}: {exc}"}
    record(signal, path=path, sandbox=sandbox, section=section)
    result: dict[str, Any] = {
        "status": status,
        "path": path,
        "section": section,
        "mutation": section_mutation_summary(prior_body, new_body),
    }
    if normalized:
        result["normalized_heading"] = True
    if warn_on_shrink and mutation_op:
        warning = shrink_warning(prior_body, new_body, op=mutation_op)
        if warning:
            result["_warning"] = warning
    return result


def _section_delete_result(
    resolved: Path,
    path: str,
    sandbox: str,
    section: str,
) -> dict[str, Any]:
    if is_converted_format(resolved):
        return {
            "error": (
                f"Cannot modify {resolved.suffix} files via section ops — "
                "converted formats are read-only (use md_list / md_read)"
            )
        }
    text, err = _load_text(resolved)
    if err:
        return {"error": err}
    try:
        prior_body = md_read_section(text, section)
    except SectionError as exc:
        return {"error": str(exc)}
    try:
        updated = md_delete_section(text, section)
    except SectionError as exc:
        return {"error": str(exc)}
    try:
        _write_file(resolved, updated)
    except OSError as exc:
        return {"error": f"Failed to write {resolved}: {exc}"}
    record(
        "mcp.tool.markdown.section.deleted",
        path=path,
        sandbox=sandbox,
        section=section,
    )
    result: dict[str, Any] = {
        "status": "deleted",
        "path": path,
        "section": section,
        "mutation": delete_mutation_summary(prior_body),
    }
    warning = delete_warning(prior_body)
    if warning:
        result["_warning"] = warning
    return result


def _pdf_section_op(
    op: str, resolved: Path, path: str, sandbox: str, section: str
) -> dict[str, Any]:
    """List/read PDF sections via the outline-driven (TOC) navigator."""
    if not resolved.exists():
        return {"error": f"File not found: {resolved.name}"}
    try:
        if op == "list_sections":
            listing = list_pdf_sections(resolved)
            record(
                "mcp.tool.markdown.sections.listed",
                path=path,
                sandbox=sandbox,
                count=len(listing["sections"]),
                source=listing["source"],
            )
            return {"path": path, "sandbox": sandbox, **listing}
        if op == "to_dict":
            data = pdf_to_dict(resolved)
            record(
                "mcp.tool.markdown.converted.to.dict",
                path=path,
                sandbox=sandbox,
                keys=len(data),
                source="pdf",
            )
            return {"data": data, "path": path, "sandbox": sandbox}
        body = read_pdf_section(resolved, section)
        record(
            "mcp.tool.markdown.section.read",
            path=path,
            sandbox=sandbox,
            section=section,
            chars=len(body),
            source="pdf",
        )
        return {"content": body, "path": path, "sandbox": sandbox, "section": section}
    except PdfSectionError as e:
        return {"error": str(e)}
    except Exception as e:  # pymupdf open/parse failures surface as a tool error
        return {"error": f"PDF section extraction failed: {e}"}


def register_markdown_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Markdown Operations")
    def markdown(
        op: str,
        path: str,
        sandbox: str = "context",
        section: str = "",
        content: str = "",
        heading: str = "",
        level: int = 0,
        position: str = "",
    ) -> dict[str, Any]:
        """Section-level markdown: list/read/replace/append/insert/delete/to_dict/from_dict.

        Sandboxes: context → tasks/; cortex → /data/files; workspaces → project root.
        Section path from list_sections; read ops with empty/omitted section return
        the full document; write ops use "" for the preamble. from_dict: content is JSON.
        Prefer over whole-file context/cortex/workspaces for long structured docs.
        Use workspaces sandbox for tmp/ files (debrief log, phase docs, handoff docs).

        PDF, DOCX, ODT, EML, and HTML files are auto-converted to markdown for
        read ops (list_sections, read_section, to_dict). Write ops
        (replace/append/delete/from_dict) are text-only — converted formats
        are rejected.

        PDFs use outline-driven navigation: list_sections returns the embedded
        TOC entries (source=pdf_toc, boundary_precision=coordinate), read_section
        clips the page region between outline anchors, and to_dict nests those
        sections by heading (parent bodies under _content). PDFs with
        no outline fall back to one section per page (source=pdf_page_fallback,
        boundary_precision=page) so reads stay bounded. A PDF section read is a
        coordinate-clipped region, not an exact markdown slice.
        """
        if not op:
            return {"error": "'op' is required"}
        if not path:
            return {"error": "'path' is required"}
        try:
            resolved = _resolve_sandbox(sandbox, path)
        except ValueError as e:
            return {"error": str(e)}

        if (
            op in ("list_sections", "read_section", "to_dict")
            and resolved.suffix.lower() == ".pdf"
        ):
            return _pdf_section_op(op, resolved, path, sandbox, section)

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
                if section.strip() == "":
                    record(
                        "mcp.tool.markdown.section.read",
                        path=path,
                        sandbox=sandbox,
                        section=None,
                        chars=len(text),
                        selection="full_document",
                    )
                    return {
                        "content": text,
                        "path": path,
                        "sandbox": sandbox,
                        "section": None,
                        "selection": "full_document",
                    }
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
                mutation_op="md_replace",
                warn_on_shrink=True,
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
        if op == "insert_section":
            if not heading:
                return {"error": "'heading' is required for insert_section"}
            return _section_write_result(
                resolved,
                path,
                sandbox,
                section or heading,
                "mcp.tool.markdown.section.inserted",
                "inserted",
                lambda t: md_insert_section(
                    t, heading, level, position, section or None, content
                ),
            )
        if op == "delete_section":
            return _section_delete_result(resolved, path, sandbox, section)
        if op == "from_dict":
            if is_converted_format(resolved):
                return {
                    "error": (
                        f"Cannot write to {resolved.suffix} files — "
                        "converted formats are read-only"
                    )
                }
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
                "replace_section, append_section, insert_section, delete_section, "
                "to_dict, from_dict"
            )
        }
