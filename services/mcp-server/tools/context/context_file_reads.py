"""Context file read MCP tools: list_context_directory and read_context_file.

Supports text and binary (with auto-detection via extension/magic). Delegates binary
handling and text extraction to .._file_helpers. Uses tasks_path_policy for safe
resolution under the tasks root. All accesses record telemetry via mcp_events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_events import record
from universal_logging import get_logger

from .._file_helpers import (
    BINARY_EXTENSIONS,
    _is_binary_by_magic,
    build_binary_read_result,
    extract_text_content,
)
from .tasks_path_policy import _TASKS_ROOT, _safe_tasks_path

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def register_context_file_read_tools(mcp: FastMCP) -> None:
    """Register read-only context directory and file-reading MCP tools."""

    @mcp.tool(title="List Context Directory")
    def list_context_directory(path: str = "") -> dict[str, list[str] | str]:
        """List files and directories under the tasks/ workspace context.

        Use this to discover what's available: journal/, discoveries/,
        lessons/, specs/, faq/, etc.

        Args:
            path: Relative path within tasks/ (empty = root).

        Returns:
            {"entries": ["<name>", ...]}
        """
        target = _safe_tasks_path(path) if path else _TASKS_ROOT
        if not target.exists():
            return {"error": f"Path not found: {path}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {path}"}

        entries = sorted(p.name for p in target.iterdir())
        logger.info(
            "list_context_directory: %s → %d entries", path or "/", len(entries)
        )
        record(
            "mcp.tool.context.directory.listed", path=path or "/", count=len(entries)
        )
        return {"entries": entries}

    @mcp.tool(title="Read Context File")
    def read_context_file(path: str, binary: bool = False) -> dict[str, Any]:
        """Read a file from the tasks/ workspace context.

        Use list_context_directory() to discover available files first.

        Use ``binary=True`` when another tool needs raw bytes rather than
        decoded text.

        Args:
            path: Relative file path within tasks/ (e.g. "discoveries/index.yaml").
            binary: If True, return base64 bytes instead of decoded text.

        Returns:
            Text mode: {"content": "<file contents>", "path": "<relative path>"}
            Binary mode: {"content_base64", "mime_type", "encoding", "bytes", "path"}
        """
        target = _safe_tasks_path(path)
        if not target.exists():
            return {"error": f"File not found: {path}"}
        if not target.is_file():
            return {"error": f"Not a file: {path}"}

        if binary:
            result = build_binary_read_result(target, path_value=path)
            logger.info(
                "read_context_file: %s (%d bytes, binary)", path, result["bytes"]
            )
            record(
                "mcp.tool.context.file.read",
                path=path,
                bytes=result["bytes"],
                binary=True,
            )
            return result

        suffix = target.suffix.lower()
        if suffix in BINARY_EXTENSIONS or _is_binary_by_magic(target):
            result = build_binary_read_result(target, path_value=path)
            result["auto_binary"] = True
            if result.get("mime_type", "").startswith("image/"):
                result["_next"] = (
                    f'For text extraction: dispatch(tool="document_ocr", '
                    f'arguments=\'{{"path": "{path}"}}\').'
                    f' For visual inspection: view_image(path="{path}").'
                )
            logger.info(
                "read_context_file: %s (%d bytes, auto_binary)", path, result["bytes"]
            )
            record(
                "mcp.tool.context.file.read",
                path=path,
                bytes=result["bytes"],
                binary=True,
                auto_binary=True,
            )
            return result

        content = extract_text_content(target)
        logger.info("read_context_file: %s (%d chars)", path, len(content))
        record(
            "mcp.tool.context.file.read", path=path, chars=len(content), binary=False
        )
        return {"content": content, "path": path}
