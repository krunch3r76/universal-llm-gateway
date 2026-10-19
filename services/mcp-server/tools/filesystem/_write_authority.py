"""Two-class cortex write authority (consult vs shared documents).

Bind: ``cortex://notes/system/threads/6655-consult-artifact-uri-collision-cdp-opus-architecture-bind.md``
Amended: ``…-cursor-sdk-amendment-from-terra-check.md`` (full execution_id, not exec8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tools._durable_write import (
    HouseRulesSizeRejectedError as HouseRulesSizeRejectedError,
)
from tools._durable_write import (
    durable_write_text,
)
from tools._hashing import format_sha256_uri, normalize_sha256_hex, sha256_hex_of_bytes

ArtifactClass = Literal["consult", "shared", "unclassified"]

HOUSE_RULES_MAX_BYTES = 8192
RULINGS_ARCHIVE_REL = "notes/system/threads/12286-rulings-archive.md"
RULINGS_ARCHIVE_URI = f"cortex://{RULINGS_ARCHIVE_REL}"

_HOUSE_RULES_SECTION = "HOUSE RULES"
_RULING_LINE = re.compile(r"^- (K\d+|a:\d+|\|)")


@dataclass(frozen=True)
class HouseRulesRotationResult:
    trimmed_text: str
    rotated_rows: list[str]
    archive_uri: str
    archive_written_sha256: str


def _split_house_rules_section(text: str) -> tuple[str, str, str]:
    marker = f"## {_HOUSE_RULES_SECTION}"
    idx = text.find(marker)
    if idx < 0:
        return text, "", ""
    after_marker = idx + len(marker)
    rest = text[after_marker:]
    if rest.startswith("\r\n"):
        body_start = after_marker + 2
    elif rest.startswith("\n"):
        body_start = after_marker + 1
    else:
        body_start = after_marker
    next_heading = re.search(r"\n##[^#]", text[body_start:])
    if next_heading:
        section_end = body_start + next_heading.start() + 1
    else:
        section_end = len(text)
    prefix = text[:body_start]
    section_body = text[body_start:section_end]
    suffix = text[section_end:]
    return prefix, section_body, suffix


def _ruling_line_indices(section_body: str) -> list[int]:
    lines = section_body.splitlines(keepends=True)
    indices: list[int] = []
    for i, line in enumerate(lines):
        if _RULING_LINE.match(line.rstrip("\r\n")):
            indices.append(i)
    return indices


def _ruling_k_number(line: str) -> int | None:
    match = re.match(r"^- K(\d+)", line.strip())
    if not match:
        return None
    return int(match.group(1))


def _oldest_ruling_line_index(lines: list[str], indices: list[int]) -> int | None:
    """Pick the lowest K-id row; ties fall back to the last bullet in the section."""
    scored: list[tuple[int, int]] = []
    for idx in indices:
        kid = _ruling_k_number(lines[idx])
        if kid is not None:
            scored.append((kid, idx))
    if scored:
        return min(scored, key=lambda pair: pair[0])[1]
    return indices[-1] if indices else None


def _trim_oldest_rulings(text: str, *, limit_bytes: int) -> tuple[str, list[str]]:
    """Remove oldest ruling rows from the HOUSE RULES section until *text* fits."""
    rotated: list[str] = []
    current = text
    while len(current.encode("utf-8")) > limit_bytes:
        prefix, section_body, suffix = _split_house_rules_section(current)
        if not section_body:
            break
        lines = section_body.splitlines(keepends=True)
        indices = _ruling_line_indices(section_body)
        drop_idx = _oldest_ruling_line_index(lines, indices)
        if drop_idx is None:
            break
        row = lines.pop(drop_idx)
        rotated.append(row.rstrip("\r\n"))
        section_body = "".join(lines)
        current = prefix + section_body + suffix
    return current, rotated


def _format_archive_block(row: str) -> str:
    heading = row.strip()
    kid = "rotated"
    match = re.match(r"^- (K\d+)", heading)
    if match:
        kid = match.group(1)
    return f"\n#### {kid} — house-rules rotation\n{row.strip()}\n"


def rotate_house_rules_to_fit(
    *,
    house_rules_text: str,
    archive_path: Path,
    limit_bytes: int = HOUSE_RULES_MAX_BYTES,
) -> HouseRulesRotationResult | None:
    """Archive oldest ruling rows until *house_rules_text* is within *limit_bytes*.

    Writes the archive first; on archive failure the house-rules text is untouched.
    Returns None when no rotation was required.
    """
    if len(house_rules_text.encode("utf-8")) <= limit_bytes:
        return None
    trimmed, rotated = _trim_oldest_rulings(house_rules_text, limit_bytes=limit_bytes)
    if not rotated or len(trimmed.encode("utf-8")) > limit_bytes:
        return None
    append_body = "".join(_format_archive_block(row) for row in rotated)
    prior = archive_path.read_text(encoding="utf-8") if archive_path.is_file() else ""
    archive_image = prior + append_body
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_written_sha256 = durable_write_text(archive_path, archive_image)
    except OSError:
        return None
    return HouseRulesRotationResult(
        trimmed_text=trimmed,
        rotated_rows=rotated,
        archive_uri=RULINGS_ARCHIVE_URI,
        archive_written_sha256=format_sha256_uri(archive_written_sha256),
    )


def is_house_rules_path(path: str) -> bool:
    """True when *path* is a shared house-rules document subject to the hop budget."""
    return Path(_norm_rel(path)).name.endswith("-house-rules.md")


def evaluate_house_rules_post_edit_size(
    *,
    path: str,
    post_edit_text: str,
    before_len: int | None = None,
    dest_exists: bool = True,
) -> dict[str, Any] | None:
    """Return a rejection payload when a house-rules post-edit image must not land.

    Ungated paths (including ``12286-rulings-archive.md``) return None. Whole-file
    and section RMW callers pass ``before_len`` from the locked pre-image so a
    non-growing edit that stays over budget still lands (#649).
    """
    if not is_house_rules_path(path):
        return None
    post_edit_bytes = len(post_edit_text.encode("utf-8"))
    if post_edit_bytes <= HOUSE_RULES_MAX_BYTES:
        return None
    if dest_exists and before_len is not None and post_edit_bytes <= before_len:
        return None
    rel = _norm_rel(path)
    return {
        "error": (
            f"Refusing write to {rel!r}: post-edit size {post_edit_bytes} B exceeds "
            f"{HOUSE_RULES_MAX_BYTES} B hop budget while growing an over-budget "
            f"file. Use rotation — move completed wording verbatim to "
            f"{RULINGS_ARCHIVE_URI} under K-id headings, then leave a compressed "
            "binding row in house-rules."
        ),
        "reason": "house_rules.over_budget",
        "path": rel,
        "artifact_class": "shared",
        "post_edit_bytes": post_edit_bytes,
        "limit_bytes": HOUSE_RULES_MAX_BYTES,
    }


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
    if base.endswith("-standing-handoff.md") or base.endswith("-house-rules.md"):
        return "shared"
    # `-consult-` marks thread consult artifacts. Runbook commands such as
    # bus-consult-watcher.md live under notes/runbooks/ and stay editable.
    if rel.startswith("notes/runbooks/"):
        return "unclassified"
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
