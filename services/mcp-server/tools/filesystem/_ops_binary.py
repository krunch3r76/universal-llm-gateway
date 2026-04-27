"""Binary file operation implementations: write_binary, append_binary."""

from __future__ import annotations

import base64
import binascii
import logging
import os
from typing import Any

from mcp_events import record

from ._paths import _BINARY_MAX_BYTES, _safe_path

logger = logging.getLogger(__name__)


def write_binary_impl(rel_path: str, content_base64: str) -> dict[str, Any]:
    """Decode base64 and write binary bytes to the sandbox atomically.

    No extension restrictions — the container sandbox is the security
    boundary. Any file type is accepted.
    """
    dest = _safe_path(rel_path)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        record(
            "mcp.tool.file.write_failed",
            path=rel_path,
            reason="invalid_base64",
        )
        raise ValueError("content is not valid base64") from exc

    if len(raw) > _BINARY_MAX_BYTES:
        raise ValueError(
            f"Decoded binary ({len(raw)} bytes) exceeds "
            f"{_BINARY_MAX_BYTES // (1024 * 1024)}MB limit"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest.with_suffix(dest.suffix + ".tmp")
    try:
        temp_path.write_bytes(raw)
        os.replace(temp_path, dest)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    record(
        "mcp.tool.file.written",
        path=rel_path,
        resolved=str(dest),
        bytes=len(raw),
        binary=True,
    )
    logger.debug("write_binary: wrote %s (%d bytes)", dest, len(raw))
    return {"status": "written", "path": str(dest), "bytes": len(raw)}


def append_binary_impl(rel_path: str, content_base64: str) -> dict[str, Any]:
    """Decode base64 and append binary bytes to an existing sandbox file.

    Creates the file if it doesn't exist. Each chunk must be independently
    valid base64. The total file size is capped at _BINARY_MAX_BYTES.
    """
    dest = _safe_path(rel_path)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        record(
            "mcp.tool.file.write_failed",
            path=rel_path,
            reason="invalid_base64",
        )
        raise ValueError("content is not valid base64") from exc

    current_size = dest.stat().st_size if dest.exists() else 0
    if current_size + len(raw) > _BINARY_MAX_BYTES:
        raise ValueError(
            f"Appending {len(raw)} bytes to {current_size}-byte file "
            f"would exceed {_BINARY_MAX_BYTES // (1024 * 1024)}MB limit"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("ab") as fh:
        fh.write(raw)

    final_size = dest.stat().st_size
    record(
        "mcp.tool.file.written",
        path=rel_path,
        resolved=str(dest),
        bytes=len(raw),
        total_bytes=final_size,
        binary=True,
        append=True,
    )
    logger.debug(
        "append_binary: appended %d bytes to %s (total %d)",
        len(raw),
        dest,
        final_size,
    )
    return {
        "status": "appended",
        "path": str(dest),
        "bytes_appended": len(raw),
        "total_bytes": final_size,
    }
