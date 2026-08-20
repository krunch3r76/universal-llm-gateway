"""Two-class cortex write authority (consult vs shared documents).

Bind: ``cortex://notes/system/threads/6655-consult-artifact-uri-collision-cdp-opus-architecture-bind.md``
Amended: ``…-cursor-sdk-amendment-from-terra-check.md`` (full execution_id, not exec8).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from tools._hashing import format_sha256_uri, normalize_sha256_hex, sha256_hex_of_bytes

ArtifactClass = Literal["consult", "shared", "unclassified"]

# S1 funnelled the former Path.write_text bypass set onto durable_io.atomic.
# Empty: leftover notes-tree write_text is a defect, not a silent carve-out.
EXCLUDED_BYPASS_WRITERS: tuple[str, ...] = ()

_SHARED_PREFIXES = (
    "notes/system/specs/",
    "notes/system/roadmaps/",
    "notes/system/ledgers/",
)
_SHARED_NAME_MARKERS = ("roadmap", "scoreboard", "charter-ledger")
_CONSULT_NAME_MARKERS = (
    "-architecture-bind",
    "-independent-check",
    "-fable-answer",
    "fable-answer.md",
    "opus-grok-instructions",
    "cdp-ask-archive",
    "-consult-",
    "-amendment-from-",
)


def _norm_rel(path: str) -> str:
    return path.lstrip("/").replace("\\", "/")


def sanitize_path_token(raw: str, *, fallback: str = "unknown") -> str:
    """Sanitize a seat or execution id for path embedding (full id, not truncated)."""
    token = re.sub(r"[^a-zA-Z0-9._-]+", "-", (raw or "").strip()).strip("-._")
    return token or fallback


def mint_consult_artifact_rel_path(
    *,
    thread: str,
    slug: str,
    seat: str,
    execution_id: str,
    kind: str = "architecture",
) -> str:
    """Mint a consult-class relative path with seat + full execution id."""
    if not execution_id or not str(execution_id).strip():
        raise ValueError("execution_id is required for consult-class mint")
    thread_t = sanitize_path_token(thread, fallback="thread")
    slug_t = sanitize_path_token(slug, fallback="slug")
    seat_t = sanitize_path_token(seat, fallback="seat")
    exec_t = sanitize_path_token(execution_id, fallback="execution")
    kind_t = sanitize_path_token(kind, fallback="answer")
    return f"notes/system/threads/{thread_t}-{slug_t}-{seat_t}-{exec_t}-{kind_t}.md"


def classify_artifact_path(
    path: str,
    *,
    artifact_class: str | None = None,
) -> ArtifactClass:
    """Classify a cortex-relative path into consult / shared / unclassified."""
    if artifact_class in ("consult", "shared"):
        return artifact_class  # type: ignore[return-value]
    rel = _norm_rel(path).lower()
    base = Path(rel).name
    if any(rel.startswith(prefix) for prefix in _SHARED_PREFIXES):
        return "shared"
    if any(marker in base for marker in _SHARED_NAME_MARKERS):
        return "shared"
    if any(marker in base or marker in rel for marker in _CONSULT_NAME_MARKERS):
        return "consult"
    return "unclassified"


def render_collision_pointer(
    *,
    path: str,
    existing_sha256: str,
    attempted_sha256: str,
    existing_author: str | None = None,
    attempted_author: str | None = None,
) -> str:
    """Render a fork-pointer body naming both blobs; neither is the bind alone."""
    existing = format_sha256_uri(existing_sha256)
    attempted = format_sha256_uri(attempted_sha256)
    existing_author_s = existing_author or "unknown"
    attempted_author_s = attempted_author or "unknown"
    return (
        "# FORK POINTER — not an architecture bind\n\n"
        "**Status:** collision site · neither peer is authorized as *the* bind "
        "from this address alone.\n\n"
        f"**Collision URI:** `cortex://{_norm_rel(path)}`\n\n"
        "| Peer | Author | Content sha256 |\n"
        "|---|---|---|\n"
        f"| A (incumbent) | {existing_author_s} | `{existing}` |\n"
        f"| B (refused write) | {attempted_author_s} | `{attempted}` |\n\n"
        "**Reader rule:** do not implement from this URI. Adjudicate between "
        "peer digests (content-store) or commission a fresh bind at a "
        "non-colliding seat+full-execution_id address.\n"
    )


def evaluate_write_authority(
    *,
    path: str,
    content: str,
    dest_exists: bool,
    actual_sha256: str | None,
    expected_sha256: str | None,
    if_absent: bool,
    artifact_class: str | None = None,
    author: str | None = None,
) -> dict[str, Any] | None:
    """Return a rejection payload when class rules block the write; else None."""
    klass = classify_artifact_path(path, artifact_class=artifact_class)
    if klass == "shared" and dest_exists and expected_sha256 is None:
        return {
            "error": (
                f"Refusing shared-document write to {path!r}: "
                "expected_sha256 is mandatory on this class"
            ),
            "reason": "expected_sha256.required",
            "path": _norm_rel(path),
            "artifact_class": "shared",
            "actual_sha256": (
                None if actual_sha256 is None else format_sha256_uri(actual_sha256)
            ),
        }
    if klass != "consult":
        return None
    if not dest_exists:
        return None
    # Consult-class: unguarded overwrite is indefensible; if_absent is the gate.
    if not if_absent:
        return {
            "error": (
                f"Refusing consult-class overwrite of {path!r}: "
                "use if_absent=true (create-only) or a distinct seat+"
                "execution_id address"
            ),
            "reason": "consult_class.unguarded_overwrite",
            "path": _norm_rel(path),
            "artifact_class": "consult",
            "existing_sha256": (
                None if actual_sha256 is None else format_sha256_uri(actual_sha256)
            ),
            "existing_author": author,
        }
    attempted_sha = sha256_hex_of_bytes(content.encode("utf-8"))
    existing_hex = (
        None if actual_sha256 is None else normalize_sha256_hex(actual_sha256)
    )
    return {
        "error": (f"Refusing to overwrite existing consult-class file: {path!r}"),
        "reason": "file_exists",
        "path": _norm_rel(path),
        "artifact_class": "consult",
        "existing_sha256": (
            None if existing_hex is None else format_sha256_uri(existing_hex)
        ),
        "attempted_sha256": format_sha256_uri(attempted_sha),
        "existing_author": author,
        "install_collision_pointer": (
            existing_hex is not None and existing_hex != attempted_sha
        ),
        "pointer_body": (
            render_collision_pointer(
                path=path,
                existing_sha256=existing_hex or "",
                attempted_sha256=attempted_sha,
                existing_author=author,
            )
            if existing_hex is not None and existing_hex != attempted_sha
            else None
        ),
        "attempted_content_sha256": attempted_sha,
    }
