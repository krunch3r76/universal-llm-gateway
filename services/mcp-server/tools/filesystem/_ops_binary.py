"""Binary file operation implementations: write_binary, append_binary."""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from mcp_events import record

from .._durable_write import (
    WriteVerifyError,
    durable_write_bytes,
    verify_persisted,
    write_verify_error_dict,
)
from ._paths import BINARY_MAX_BYTES, reject_template_tokens, safe_path

logger = logging.getLogger(__name__)


def write_binary_impl(rel_path: str, content_base64: str) -> dict[str, Any]:
    """Decode base64 and write binary bytes to the sandbox atomically.

    No extension restrictions — the container sandbox is the security
    boundary. Any file type is accepted.
    """
    reject_template_tokens(rel_path)
    dest = safe_path(rel_path, for_write=True)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        record(
            "mcp.tool.file.write_failed",
            path=rel_path,
            reason="invalid_base64",
        )
        raise ValueError("content is not valid base64") from exc

    if len(raw) > BINARY_MAX_BYTES:
        raise ValueError(
            f"Decoded binary ({len(raw)} bytes) exceeds "
            f"{BINARY_MAX_BYTES // (1024 * 1024)}MB limit"
        )

    try:
        written_sha256 = durable_write_bytes(dest, raw)
        verify_persisted(dest, written_sha256)
    except WriteVerifyError as exc:
        return write_verify_error_dict(exc)

    record(
        "mcp.tool.file.written",
        path=rel_path,
        resolved=str(dest),
        bytes=len(raw),
        binary=True,
    )
    logger.debug("write_binary: wrote %s (%d bytes)", dest, len(raw))
    return {
        "status": "written",
        "path": str(dest),
        "bytes": len(raw),
        "written_sha256": written_sha256,
    }


def append_binary_impl(rel_path: str, content_base64: str) -> dict[str, Any]:
    """Decode base64 and append binary bytes to an existing sandbox file.

    Creates the file if it doesn't exist. Each chunk must be independently
    valid base64. The total file size is capped at BINARY_MAX_BYTES.
    """
    reject_template_tokens(rel_path)
    dest = safe_path(rel_path, for_write=True)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        record(
            "mcp.tool.file.write_failed",
            path=rel_path,
            reason="invalid_base64",
        )
        raise ValueError("content is not valid base64") from exc

    current = dest.read_bytes() if dest.exists() else b""
    combined = current + raw
    if len(combined) > BINARY_MAX_BYTES:
        raise ValueError(
            f"Appending {len(raw)} bytes to {len(current)}-byte file "
            f"would exceed {BINARY_MAX_BYTES // (1024 * 1024)}MB limit"
        )

    try:
        written_sha256 = durable_write_bytes(dest, combined)
        verify_persisted(dest, written_sha256)
    except WriteVerifyError as exc:
        return write_verify_error_dict(exc)

    record(
        "mcp.tool.file.written",
        path=rel_path,
        resolved=str(dest),
        bytes=len(raw),
        total_bytes=len(combined),
        binary=True,
        append=True,
    )
    logger.debug(
        "append_binary: appended %d bytes to %s (total %d)",
        len(raw),
        dest,
        len(combined),
    )
    return {
        "status": "appended",
        "path": str(dest),
        "bytes_appended": len(raw),
        "total_bytes": len(combined),
        "written_sha256": written_sha256,
    }
