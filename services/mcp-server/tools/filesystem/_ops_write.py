"""Cortex sandbox write path — CAS, class authority, durable content install."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp_events import record

from .._durable_write import (
    WriteVerifyError,
    durable_write_text,
    finalize_atomic_replace,
    temp_path_for,
    verify_persisted,
    write_verify_error_dict,
)
from .._hashing import format_sha256_uri, sha256_hex_equal
from ._content_store import retain_bytes
from ._format_writers import write_docx, write_pdf
from ._overwrite_retain import retain_before_overwrite
from ._paths import (
    path_write_lock,
    reject_template_tokens,
    safe_path,
    sha256_hex_of_file,
    sha256_of_file,
)
from ._share_uri_response import attach_dual_carry
from ._write_authority import classify_artifact_path, evaluate_write_authority

logger = logging.getLogger(__name__)


def write_rejection(
    *,
    path: str,
    resolved: Path,
    reason: str,
    message: str,
    expected_sha256: str | None = None,
    actual_sha256: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a structured write-rejection payload and emit the reject event."""
    record(
        "mcp.tool.file.write_rejected",
        path=path,
        resolved=str(resolved),
        reason=reason,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
    )
    payload: dict[str, Any] = {
        "error": message,
        "reason": reason,
        "path": path.lstrip("/"),
    }
    if expected_sha256 is not None:
        payload["expected_sha256"] = expected_sha256
    if actual_sha256 is not None:
        payload["actual_sha256"] = actual_sha256
    payload.update(extra)
    return payload


def write_content_durable(dest: Path, content: str) -> str:
    """Write *content* to *dest* with fsync + atomic replace; return sha256 hex."""
    suffix = dest.suffix.lower()
    if suffix in {".docx", ".pdf"}:
        write_handlers = {
            ".docx": write_docx,
            ".pdf": write_pdf,
        }
        temp_path = temp_path_for(dest)
        try:
            write_handlers[suffix](temp_path, content)
            finalize_atomic_replace(temp_path, dest)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        written_sha256 = sha256_hex_of_file(dest)
        verify_persisted(dest, written_sha256)
        return written_sha256
    written_sha256 = durable_write_text(dest, content)
    verify_persisted(dest, written_sha256)
    return written_sha256


def _apply_authority_rejection(
    path: str,
    dest: Path,
    content: str,
    authority: dict[str, Any],
) -> dict[str, Any]:
    """Apply consult/shared rejection, optionally installing a fork pointer."""
    pointer_body = authority.pop("pointer_body", None)
    install_pointer = bool(authority.pop("install_collision_pointer", False))
    attempted_hex = authority.pop("attempted_content_sha256", None)
    if install_pointer and pointer_body and attempted_hex:
        retain_before_overwrite(dest)
        retain_bytes(content.encode("utf-8"))
        try:
            pointer_sha = write_content_durable(dest, pointer_body)
        except (WriteVerifyError, OSError):
            logger.exception(
                "write_file: failed installing collision pointer at %s", dest
            )
            pointer_sha = None
        else:
            authority["pointer_installed"] = True
            authority["pointer_sha256"] = pointer_sha
            authority["attempted_retained_sha256"] = attempted_hex
    message = str(authority.pop("error"))
    reason = str(authority.pop("reason"))
    authority.pop("path", None)
    expected_echo = authority.pop("expected_sha256", None)
    existing_echo = authority.pop("existing_sha256", None)
    actual_echo = existing_echo or authority.pop("actual_sha256", None)
    if existing_echo is not None:
        authority["existing_sha256"] = existing_echo
    return write_rejection(
        path=path,
        resolved=dest,
        reason=reason,
        message=message,
        expected_sha256=expected_echo,
        actual_sha256=actual_echo,
        **authority,
    )


def write_file_impl(
    path: str,
    content: str,
    *,
    expected_sha256: str | None = None,
    if_absent: bool = False,
    artifact_class: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """Write *content* to *path* inside the sandboxed files directory.

    Intermediate directories are created automatically.

    CAS semantics (cortex sandbox; see friction-13695 sidecar):
      - ``expected_sha256`` absent → create-or-overwrite; overwrites echo
        ``replaced_sha256`` (bare hex of prior bytes) and retain prior content
        in ``.content-store/sha256/`` (item-15 / AC-15a–b).
      - ``expected_sha256`` present → file must exist and hash must match.
      - ``if_absent=True`` → create-only; fails when the path already exists.
      - Both guard params together → ``ValueError``.
      - Consult-class paths refuse unguarded overwrite; ``if_absent`` collisions
        name existing digest/author and may install a fork pointer.
      - Shared-document paths require ``expected_sha256`` when the file exists.

    On success, response includes ``written_sha256``: bare lowercase hex of the
    resulting file bytes. Overwrites also include ``replaced_sha256`` when prior
    bytes existed. ``expected_sha256`` accepts bare hex (``read_sha256``
    round-trip) or ``sha256:`` / ``spec_sha256:`` citation prefixes.

    A successful write to a consult-class path additionally echoes
    ``artifact_class: "consult"`` + ``consult_notice``: this class is
    write-once, so every later op on the same path (write, append, prepend,
    replace, insert_at_line, any ``md_*`` write) refuses unconditionally, CAS
    match or not (friction a:31802 — the refusal previously surfaced only on
    the doomed follow-up call, after partial content was already committed).
    """
    if expected_sha256 is not None and if_absent:
        raise ValueError("expected_sha256 and if_absent are mutually exclusive")

    reject_template_tokens(path)
    dest = safe_path(path, for_write=True)
    with path_write_lock(dest):
        actual_sha256 = sha256_of_file(dest)
        class_decision = evaluate_write_authority(
            path=path,
            content=content,
            dest_exists=dest.exists(),
            actual_sha256=actual_sha256,
            expected_sha256=expected_sha256,
            if_absent=if_absent,
            artifact_class=artifact_class,
            author=author,
        )
        if class_decision is not None:
            return _apply_authority_rejection(path, dest, content, class_decision)
        if if_absent and dest.exists():
            return write_rejection(
                path=path,
                resolved=dest,
                reason="file_exists",
                message=f"Refusing to overwrite existing file: {path!r}",
                actual_sha256=(
                    None if actual_sha256 is None else format_sha256_uri(actual_sha256)
                ),
            )
        # Prefix-normalize so read_sha256 (bare hex) round-trips into CAS write
        # without hand-editing (friction a:26153).
        if expected_sha256 is not None and not sha256_hex_equal(
            actual_sha256, expected_sha256
        ):
            expected_echo = format_sha256_uri(expected_sha256)
            actual_echo = (
                None if actual_sha256 is None else format_sha256_uri(actual_sha256)
            )
            return write_rejection(
                path=path,
                resolved=dest,
                reason="file_sha256.mismatch",
                message=(
                    f"Refusing write to {path!r}: current file hash "
                    f"{actual_echo!r} does not match expected {expected_echo!r}"
                ),
                expected_sha256=expected_echo,
                actual_sha256=actual_echo,
            )
        replaced_sha256 = retain_before_overwrite(dest)
        try:
            written_sha256 = write_content_durable(dest, content)
        except WriteVerifyError as exc:
            return write_verify_error_dict(exc)
        except OSError as exc:
            record(
                "mcp.tool.file.write_failed",
                path=path,
                resolved=str(dest),
                reason="os_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            logger.exception("write_file: OS error writing %s", dest)
            raise

    event_payload: dict[str, Any] = {
        "path": path,
        "resolved": str(dest),
        "chars": len(content),
        "written_sha256": written_sha256,
    }
    if replaced_sha256 is not None:
        event_payload["replaced_sha256"] = replaced_sha256
    record("mcp.tool.file.written", **event_payload)
    logger.debug("write_file: wrote %s (%d chars)", dest, len(content))
    rel = path.lstrip("/")
    result: dict[str, Any] = {
        "status": "written",
        "written_sha256": written_sha256,
    }
    if replaced_sha256 is not None:
        result["replaced_sha256"] = replaced_sha256
    if classify_artifact_path(path, artifact_class=artifact_class) == "consult":
        result["artifact_class"] = "consult"
        result["consult_notice"] = (
            "Write-once path: every later op here (write, append, prepend, "
            "replace, insert_at_line, any md_* write) will be refused, CAS "
            "match or not. Mint a new seat+execution_id address to revise."
        )
    return attach_dual_carry(result, sandbox="cortex", rel_path=rel)
