"""Deterministic maestro journal projection for the hop-successor body."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

from implement_admission.closeout_helpers import cortex_files_root

from claude_bundles.operator_proxy_standing_render import (
    HOP_LESSONS_CAP_B,
    HOP_REGISTER_CAP_B,
    trim_hop_block_whole_lines,
)

_ARCHIVED_PREV_RE = re.compile(
    r"^archived_prev:\s*(\S+)\s+sha256\s+([0-9a-f]{64})\s*$",
    re.MULTILINE | re.IGNORECASE,
)

MAESTRO_JOURNAL_URI: Final = "cortex://notes/system/maestro/journal.md"
JOURNAL_HEAD_BYTES: Final = 64 * 1024
MAESTRO_REGISTER_URI: Final = "cortex://notes/system/maestro/register.md"
LESSONS_K: Final = 5

MAESTRO_ROLE_FACETS: frozenset[str] = frozenset(
    {"route", "restart", "review", "brief", "bus", "land", "register"}
)

_RULE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?RULE:",
    re.MULTILINE,
)
_FACET_TAG_RE = re.compile(r"\[([a-z]+)\]")
_ENTRY_HEADING_RE = re.compile(r"(?m)^## (.+?)\s*\n")
_STANDING_RULE_HEADING = "STANDING RULE"


def role_facet_set(role: str) -> frozenset[str]:
    """Facet tags kept for *role*; only maestro has a seed set (Q2)."""
    if (role or "").strip().lower() == "maestro":
        return MAESTRO_ROLE_FACETS
    return frozenset()


def _journal_entries(text: str) -> list[tuple[str, str, int]]:
    """``(heading, body, start_offset)`` in file order (newest first at head)."""
    body = text or ""
    matches = list(_ENTRY_HEADING_RE.finditer(body))
    if not matches:
        return []
    entries: list[tuple[str, str, int]] = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        entries.append((heading, body[start:end], match.start()))
    return entries


def _line_facets(line: str) -> frozenset[str]:
    return frozenset(_FACET_TAG_RE.findall(line or ""))


def _rule_line_matches(line: str) -> bool:
    return _RULE_LINE_RE.match(line or "") is not None


def _entry_rule_lines(
    heading: str,
    body: str,
    *,
    facets: frozenset[str],
) -> list[str]:
    if _STANDING_RULE_HEADING in heading:
        return []
    kept: list[str] = []
    for line in body.splitlines():
        if not _rule_line_matches(line):
            continue
        line_facets = _line_facets(line)
        if not (line_facets & facets):
            continue
        kept.append(line.rstrip())
    return kept


def _entry_on_hop_lane(heading: str, *, lane: str | None) -> bool:
    if not lane:
        return False
    needle = f"lane {lane.strip()}"
    return needle in (heading or "")


def _lessons_blocks_from_projected(
    projected: list[tuple[str, str]],
) -> str:
    if not projected:
        return "## Lessons in force\n\n(none)\n"
    blocks: list[str] = ["## Lessons in force", ""]
    current_heading: str | None = None
    for heading, line in projected:
        if heading != current_heading:
            blocks.append(f"## {heading}")
            current_heading = heading
        blocks.append(line)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def _trim_lessons_whole_entries(text: str, max_bytes: int) -> str:
    """Drop whole lesson entries from the tail until the block fits (never byte-cut)."""
    body = text or ""
    if _utf8_len(body) <= max_bytes:
        return body
    marker = "## Lessons in force\n\n"
    if not body.startswith(marker):
        return trim_hop_block_whole_lines(body, max_bytes)
    tail = body[len(marker) :]
    parts = tail.split("\n## ")
    if not parts:
        return marker + "(none)\n"
    preamble = parts[0]
    entries: list[str] = []
    for chunk in parts[1:]:
        entries.append("## " + chunk)
    while entries and _utf8_len(marker + preamble + "".join(entries)) > max_bytes:
        entries.pop()
    if not entries and preamble.strip() in ("", "(none)"):
        return "## Lessons in force\n\n(none)\n"
    rebuilt = marker + preamble
    if entries:
        if not rebuilt.endswith("\n"):
            rebuilt += "\n"
        rebuilt += "\n".join(entries)
        if not rebuilt.endswith("\n"):
            rebuilt += "\n"
    return rebuilt


def project_lessons_in_force(
    journal_text: str,
    *,
    role: str = "maestro",
    k: int = LESSONS_K,
    lane: str | None = None,
) -> str:
    """Render ``## Lessons in force`` from full journal text."""
    facets = role_facet_set(role)
    if not (journal_text or "").strip():
        return "## Lessons in force\n\n(none)\n"
    hop_entries: list[tuple[str, list[str]]] = []
    other_entries: list[tuple[str, list[str]]] = []
    for heading, body, _offset in _journal_entries(journal_text):
        lines = _entry_rule_lines(heading, body, facets=facets)
        if not lines:
            continue
        bucket = hop_entries if _entry_on_hop_lane(heading, lane=lane) else other_entries
        bucket.append((heading, lines))
    chosen: list[tuple[str, list[str]]] = []
    for group in (hop_entries, other_entries):
        for heading, lines in group:
            chosen.append((heading, lines))
            if len(chosen) >= k:
                break
        if len(chosen) >= k:
            break
    if not chosen:
        return "## Lessons in force\n\n(none)\n"
    projected: list[tuple[str, str]] = []
    for heading, lines in chosen:
        for line in lines:
            projected.append((heading, line))
    return _lessons_blocks_from_projected(projected)


def render_register_card(
    register_text: str | None,
    *,
    register_uri: str = MAESTRO_REGISTER_URI,
    max_lines: int = 8,
) -> str:
    """Render ``## Register`` — whole card capped at *max_lines*, or absence line."""
    if register_text is None:
        return f"## Register\n\nRegister card absent at {register_uri}.\n"
    lines = [ln for ln in (register_text or "").splitlines() if ln.strip()]
    if not lines:
        return f"## Register\n\nRegister card absent at {register_uri}.\n"
    capped = "\n".join(lines[:max_lines])
    return f"## Register\n\n{capped}\n"


ReadJournalHead = Callable[[str, int], tuple[str | None, int | None]]


def _utf8_len(text: str) -> int:
    return len((text or "").encode("utf-8"))


def _decode_utf8_head(raw: bytes) -> str:
    cut = raw
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore")


def _trim_head_past_last_newline(text: str | None) -> str | None:
    """Drop an incomplete trailing line when the file extends past the head read."""
    if not text:
        return text
    last_nl = text.rfind("\n")
    if last_nl < 0:
        return ""
    return text[: last_nl + 1]


def _read_path_journal_head(path: Path, limit: int) -> tuple[str | None, int | None]:
    with path.open("rb") as handle:
        head = handle.read(limit)
        handle.seek(0, 2)
        total = handle.tell()
    if not head and total == 0:
        return "", 0
    return _decode_utf8_head(head), total


def _journal_preamble(head_text: str) -> str:
    body = head_text or ""
    match = _ENTRY_HEADING_RE.search(body)
    if match is None:
        return body
    return body[: match.start()]


def journal_over_budget_flag(total_bytes: int) -> str:
    """The single over-budget line ``_journal_head_flags`` emits."""
    return (
        f"[journal_over_budget {total_bytes}/{JOURNAL_HEAD_BYTES}"
        " — rotation owed at next hop]"
    )


def _journal_head_flags(
    head_text: str,
    *,
    total_bytes: int | None,
    archive_path_exists: Callable[[str], bool] | None,
) -> list[str]:
    flags: list[str] = []
    if total_bytes is not None and total_bytes > JOURNAL_HEAD_BYTES:
        flags.append(journal_over_budget_flag(total_bytes))
    preamble = _journal_preamble(head_text)
    archived = _ARCHIVED_PREV_RE.search(preamble)
    if not archived:
        return flags
    path = archived.group(1)
    if archive_path_exists is None:
        return flags
    try:
        missing = not archive_path_exists(path)
    except Exception as exc:  # noqa: BLE001 — unreadable ≠ absent; fail-open
        flags.append(
            f"[render_failed archive_path: {type(exc).__name__} — read {path}]"
        )
        return flags
    if missing:
        flags.append("archive_missing: journal")
    return flags


def _prepend_journal_flags(lessons: str, flags: list[str]) -> str:
    if not flags:
        return lessons
    prefix = "\n".join(flags)
    block = "## Lessons in force\n\n"
    if lessons.startswith(block):
        tail = lessons[len(block) :]
        return f"{block}{prefix}\n\n{tail}"
    return f"{prefix}\n\n{lessons}"


def _read_cortex_uri(
    uri: str,
    *,
    read_uri: Callable[[str], str | None] | None = None,
) -> str | None:
    try:
        if read_uri is not None:
            return read_uri(uri)
        rel = uri.removeprefix("cortex://").lstrip("/")
        path = cortex_files_root() / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def load_journal_text_for_hop(
    *,
    journal_text: str | None = None,
    read_uri: Callable[[str], str | None] | None = None,
    read_journal_head: ReadJournalHead | None = None,
    journal_uri: str = MAESTRO_JOURNAL_URI,
) -> tuple[str | None, str | None, int | None]:
    """Return ``(head_text, error_token, total_bytes)``.

    Reads at most ``JOURNAL_HEAD_BYTES`` from the journal file head. ``error_token``
    is ``unavailable`` on read failure. ``read_uri`` is ignored for journal load
    (register reads still use it elsewhere).
    """
    del read_uri  # journal load is always head-bounded via path or read_journal_head
    if journal_text is not None:
        return journal_text, None, _utf8_len(journal_text)
    try:
        if read_journal_head is not None:
            text, total = read_journal_head(journal_uri, JOURNAL_HEAD_BYTES)
        else:
            rel = journal_uri.removeprefix("cortex://").lstrip("/")
            path = cortex_files_root() / rel
            if not path.is_file():
                return None, None, None
            text, total = _read_path_journal_head(path, JOURNAL_HEAD_BYTES)
        if total is not None and total > JOURNAL_HEAD_BYTES:
            text = _trim_head_past_last_newline(text)
        return text, None, total
    except (OSError, UnicodeError):
        return None, "unavailable", None


def hop_journal_projection_fields(
    *,
    journal_text: str | None = None,
    register_text: str | None = None,
    read_uri: Callable[[str], str | None] | None = None,
    read_journal_head: ReadJournalHead | None = None,
    archive_path_exists: Callable[[str], bool] | None = None,
    role: str = "maestro",
    lane: str | None = None,
    inflight_projection_lookup: Callable[[], str | None] | None = None,
) -> dict[str, str]:
    """Build hop successor journal fields: ``lessons_in_force`` (from journal head
    via ``project_lessons_in_force``, plus head flags and optional in-flight
    projection line), ``register_card``, and when known ``journal_head_bytes`` /
    ``journal_over_budget_flag``."""
    text, err, total_bytes = load_journal_text_for_hop(
        journal_text=journal_text,
        read_uri=read_uri,
        read_journal_head=read_journal_head,
    )
    inflight_line = ""
    if inflight_projection_lookup is not None:
        try:
            inflight_id = inflight_projection_lookup()
        except OSError:
            inflight_id = None
        if inflight_id:
            inflight_line = f"In-flight journal projection: {inflight_id}\n\n"

    if err == "unavailable":
        lessons = "## Lessons in force\n\njournal unavailable\n"
    elif text is None:
        lessons = project_lessons_in_force("", role=role, lane=lane)
    else:
        lessons = project_lessons_in_force(text, role=role, lane=lane)

    over_budget_flag = ""
    try:
        flags = _journal_head_flags(
            text or "",
            total_bytes=total_bytes,
            archive_path_exists=archive_path_exists,
        )
        over_budget_flag = next(
            (flag for flag in flags if flag.startswith("[journal_over_budget ")),
            "",
        )
        lessons = _prepend_journal_flags(lessons, flags)
    except Exception:  # noqa: BLE001 — flags must not abort hop projection
        pass

    if inflight_line:
        prefix = "## Lessons in force\n\n"
        if lessons.startswith(prefix):
            tail = lessons[len(prefix) :]
            lessons = f"{prefix}{inflight_line}{tail}"
        else:
            lessons = lessons.rstrip() + "\n\n" + inflight_line

    if register_text is not None:
        register = render_register_card(register_text)
    else:
        try:
            reg_body = _read_cortex_uri(MAESTRO_REGISTER_URI, read_uri=read_uri)
        except (OSError, UnicodeError) as exc:
            register = (
                "## Register\n\n"
                f"[render_failed register_card: {type(exc).__name__} — "
                f"read {MAESTRO_REGISTER_URI}]\n"
            )
        else:
            register = render_register_card(reg_body)

    lessons = _trim_lessons_whole_entries(lessons, HOP_LESSONS_CAP_B)
    register = trim_hop_block_whole_lines(register, HOP_REGISTER_CAP_B)

    fields = {"lessons_in_force": lessons, "register_card": register}
    if total_bytes is not None:
        fields["journal_head_bytes"] = str(total_bytes)
    if over_budget_flag:
        fields["journal_over_budget_flag"] = over_budget_flag
    return fields


__all__ = [
    "JOURNAL_HEAD_BYTES",
    "LESSONS_K",
    "MAESTRO_JOURNAL_URI",
    "MAESTRO_REGISTER_URI",
    "MAESTRO_ROLE_FACETS",
    "ReadJournalHead",
    "hop_journal_projection_fields",
    "journal_over_budget_flag",
    "load_journal_text_for_hop",
    "project_lessons_in_force",
    "render_register_card",
    "role_facet_set",
]
