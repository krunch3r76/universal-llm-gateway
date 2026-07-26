"""Transcript.md ATX schema reader and writer."""

from __future__ import annotations

import re

from session_store.fence import (
    extract_fenced,
    is_closing_fence_line,
    is_fence_line,
    wrap_fenced,
)
from session_store.models import ImmutableArchiveError, SchemaError, SessionDoc, Turn

TITLE_RE = re.compile(r"^# Session (.+)$")
SECTION_RE = re.compile(r"^## (Meta|Rollup|Index|Archive Map)$")
TURN_HEADING_RE = re.compile(r"^## Turn (\d{4}) — (user|assistant)$")
TOOLS_RE = re.compile(r"^tools:\s*(.+)$")

FIXED_SECTIONS = ("Meta", "Rollup", "Index", "Archive Map")


def _parse_meta_block(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or ":" not in s:
            continue
        key, val = s.split(":", 1)
        meta[key.strip()] = val.strip()
    return meta


def _nonempty_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s and s != "(none)":
            lines.append(s)
    return lines


def _iter_sections(text: str):
    """Yield (kind, name, body_lines) for fence-aware ATX sections."""
    lines = text.split("\n")
    if not lines:
        return
    title = lines[0].strip()
    if not TITLE_RE.match(title):
        raise SchemaError(f"invalid title line: {title!r}")
    yield ("title", TITLE_RE.match(title).group(1).strip(), [])

    i = 1
    in_fence = False
    fence_char = ""
    fence_len = 0
    current_kind = ""
    current_name = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal body, current_kind, current_name
        if current_kind:
            yield_value = (current_kind, current_name, body)
            body = []
            return yield_value
        return None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if is_fence_line(stripped):
            from session_store.fence import parse_fence_open

            parsed = parse_fence_open(stripped)
            if parsed:
                ch, ln, _ = parsed
                if not in_fence:
                    in_fence = True
                    fence_char, fence_len = ch, ln
                elif is_closing_fence_line(line, fence_char, fence_len):
                    in_fence = False
                    fence_char, fence_len = "", 0
            body.append(line)
            i += 1
            continue

        if not in_fence and line.startswith("## "):
            if current_kind:
                yield current_kind, current_name, body
                body = []
            heading = stripped
            if m_turn := TURN_HEADING_RE.match(heading):
                current_kind = "turn"
                current_name = f"{m_turn.group(1)}:{m_turn.group(2)}"
            elif m_sec := SECTION_RE.match(heading):
                current_kind = "fixed"
                current_name = m_sec.group(1)
            else:
                raise SchemaError(f"unknown or malformed section heading: {heading!r}")
            i += 1
            continue

        if current_kind:
            body.append(line)
        i += 1

    if current_kind:
        yield current_kind, current_name, body


def _parse_turn_body(body_lines: list[str]) -> tuple[str | None, str]:
    lines = list(body_lines)
    while lines and lines[0].strip() == "":
        lines.pop(0)
    tools: str | None = None
    if lines and (m := TOOLS_RE.match(lines[0].strip())):
        tools = m.group(1).strip()
        lines = lines[1:]
        while lines and lines[0].strip() == "":
            lines.pop(0)
    if not lines:
        raise SchemaError("turn body must be fenced")
    if not is_fence_line(lines[0].strip()):
        raise SchemaError("turn body must be fenced")
    body = extract_fenced("\n".join(lines))
    return tools, body


def parse_transcript(text: str) -> SessionDoc:
    session_id = ""
    meta: dict[str, str] = {}
    rollup = ""
    index_lines: list[str] = []
    archive_map: list[str] = []
    turns: list[Turn] = []

    for kind, name, body_lines in _iter_sections(text):
        if kind == "title":
            session_id = name
        elif kind == "fixed":
            block = "\n".join(body_lines).strip()
            if name == "Meta":
                meta = _parse_meta_block(block)
            elif name == "Rollup":
                rollup = block
            elif name == "Index":
                index_lines = _nonempty_lines(block)
            elif name == "Archive Map":
                archive_map = _nonempty_lines(block)
        elif kind == "turn":
            turn_n_s, role = name.split(":", 1)
            tools, body = _parse_turn_body(body_lines)
            turns.append(Turn(n=int(turn_n_s), role=role, body=body, tools_digest=tools))

    if not session_id:
        raise SchemaError("missing session title")
    return SessionDoc(
        session_id=session_id,
        meta=meta,
        rollup_text=rollup,
        index_lines=index_lines,
        archive_map_lines=archive_map,
        turns=turns,
    )


def section_count(doc: SessionDoc) -> int:
    return 1 + len(FIXED_SECTIONS) + len(doc.turns)


def assert_mutable(meta: dict[str, str]) -> None:
    if meta.get("immutable", "").lower() == "true":
        raise ImmutableArchiveError("archive marked immutable: true")


def render_transcript(doc: SessionDoc, *, original: SessionDoc | None = None) -> str:
    if original is not None:
        assert_mutable(original.meta)
    parts: list[str] = [f"# Session {doc.session_id}", "", "## Meta", ""]
    meta = dict(doc.meta)
    meta.setdefault("session_id", doc.session_id)
    meta.setdefault("schema_version", "1")
    for key, val in meta.items():
        parts.append(f"{key}: {val}")
    parts.extend(["", "## Rollup", ""])
    parts.append(doc.rollup_text)
    parts.extend(["", "## Index", ""])
    parts.extend(doc.index_lines or ["(none)"])
    parts.extend(["", "## Archive Map", ""])
    parts.extend(doc.archive_map_lines or ["(none)"])
    for turn in sorted(doc.turns, key=lambda t: t.n):
        parts.extend(["", f"## Turn {turn.n:04d} — {turn.role}", ""])
        if turn.tools_digest is not None:
            parts.append(f"tools: {turn.tools_digest}")
            parts.append("")
        parts.append(wrap_fenced(turn.body))
    return "\n".join(parts) + "\n"
