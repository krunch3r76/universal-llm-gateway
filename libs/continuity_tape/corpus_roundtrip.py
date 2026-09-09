"""R2 corpus round-trip — compare extract+render to sealed verbatim prefixes."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from continuity_tape.extract_jsonl import extract_turns_from_jsonl
from continuity_tape.render_md import render_verbatim_md

_TURN_HEADING_RE = re.compile(r"^## Turn \d+ — .+$", re.M)
_ASSISTANT_SENTINEL = "(no assistant output)"


@dataclass(frozen=True)
class CorpusRoundtripReport:
    """Result of scanning sealed journal rows against live JSONL."""

    checked: int
    missing_jsonl: int
    missing_md: int
    legacy_parity_diff: int
    diff_rows: int
    prefix_extend_rows: int
    assistant_fill_rows: int
    jsonl_edited_after_seal: int

    def summary_line(self) -> str:
        return (
            f"checked={self.checked} legacy_parity_diff={self.legacy_parity_diff} "
            f"diff_rows={self.diff_rows} prefix_extend_rows={self.prefix_extend_rows} "
            f"assistant_fill_rows={self.assistant_fill_rows} "
            f"jsonl_edited_after_seal={self.jsonl_edited_after_seal} "
            f"missing_jsonl={self.missing_jsonl} missing_md={self.missing_md}"
        )


def _default_transcripts_root() -> Path:
    override = os.environ.get("CURSOR_AGENT_TRANSCRIPTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path.home()
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-universal-llm-gateway"
        / "agent-transcripts"
    ).resolve()


def _default_files_root() -> Path:
    override = os.environ.get("CORTEX_FILES_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "mcp-data" / "files"


def _default_cortex_db() -> Path:
    override = os.environ.get("CORTEX_DB_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cortex" / "cortex.db"


def _split_turn_blocks(verbatim_md: str) -> list[tuple[str, str, str]]:
    lines = verbatim_md.splitlines()
    turns: list[tuple[str, str, str]] = []
    idx = 0
    while idx < len(lines):
        if not lines[idx].startswith("## Turn "):
            idx += 1
            continue
        heading = lines[idx]
        idx += 1
        if idx < len(lines) and lines[idx].strip() == "":
            idx += 1
        user = assistant = ""
        if idx < len(lines) and lines[idx] == "### User":
            idx += 2
            user_lines: list[str] = []
            while idx < len(lines) and not lines[idx].startswith("### "):
                user_lines.append(lines[idx])
                idx += 1
            user = "\n".join(user_lines)
        if idx < len(lines) and lines[idx].startswith("### "):
            idx += 1
            if idx < len(lines) and lines[idx].strip() == "":
                idx += 1
            asst_lines: list[str] = []
            while idx < len(lines) and not lines[idx].startswith("## Turn "):
                asst_lines.append(lines[idx])
                idx += 1
            assistant = "\n".join(asst_lines)
        turns.append((heading, user, assistant))
    return turns


def _assistant_sentinel_only_diff(sealed: str, rendered: str) -> bool:
    sealed_turns = _split_turn_blocks(sealed)
    rendered_turns = _split_turn_blocks(rendered)
    if len(sealed_turns) != len(rendered_turns):
        return False
    for (_, su, sa), (_, ru, ra) in zip(sealed_turns, rendered_turns):
        if su != ru:
            return False
        if sa == ra:
            continue
        if sa.strip() == _ASSISTANT_SENTINEL and ra.strip() and ra.strip() != _ASSISTANT_SENTINEL:
            continue
        return False
    return True


def sealed_roundtrip_holds(*, sealed: str, rendered: str, legacy: str) -> str | None:
    """Return None when round-trip holds; otherwise a short reason label.

    ``jsonl_edited_after_seal`` — live JSONL re-extract matches the legacy
    assembler but diverges from the sealed on-disk verbatim (§9.1.1). Counted
    explicitly in ``CorpusRoundtripReport.jsonl_edited_after_seal``; not a
    silent PASS and not folded into ``diff_rows``.
    """
    if rendered == sealed:
        return None
    if rendered.startswith(sealed):
        return "prefix_extend"
    if _assistant_sentinel_only_diff(sealed, rendered):
        return "assistant_fill"
    if rendered == legacy and rendered != sealed:
        return "jsonl_edited_after_seal"
    return "mismatch"


def _legacy_assemble(path: Path, session_id: str) -> str:
    """Reproduce deleted ``assemble_verbatim_md`` for parity checks."""
    import json

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                records.append(json.loads(stripped))

    turns: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for record in records:
        role = record.get("role")
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "user":
            parts: list[str] = []
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    and block["text"].strip()
                ):
                    parts.append(block["text"])
            user_text = "\n\n".join(parts).strip()
            if not user_text:
                continue
            if current is not None:
                turns.append(current)
            current = {"user": user_text, "assistant": ""}
        elif role == "assistant":
            if current is None:
                current = {"user": "(no user message)", "assistant": ""}
            asst_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    asst_parts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    asst_parts.append(f"[tool call: {block.get('name', '<unknown>')}]")
            asst = "\n\n".join(asst_parts).strip()
            if current["assistant"]:
                current["assistant"] = f"{current['assistant']}\n\n{asst}".strip()
            else:
                current["assistant"] = asst
    if current is not None:
        turns.append(current)

    def topic_hint(user_text: str) -> str:
        flat = " ".join(user_text.split())
        if len(flat) <= 60:
            return flat or "(no user text)"
        return flat[:60].rstrip() + "…"

    lines = [f"# Transcript: {session_id}", ""]
    for idx, turn in enumerate(turns, start=1):
        topic = topic_hint(turn["user"])
        lines.extend(
            [
                f"## Turn {idx} — {topic}",
                "",
                "### User",
                "",
                turn["user"],
                "",
                "### Assistant",
                "",
                turn["assistant"] or _ASSISTANT_SENTINEL,
                "",
            ]
        )
    return "\n".join(lines)


def check_corpus_roundtrip(
    *,
    cortex_db: Path | None = None,
    files_root: Path | None = None,
    transcripts_root: Path | None = None,
    assistant_label: str = "Assistant",
) -> CorpusRoundtripReport:
    """Scan sealed rows and compare render output to stored verbatim prefixes."""
    from cortex_store.verbatim_succession import (
        journal_verbatim_bytes,
        split_verbatim_layer,
    )

    db_path = cortex_db or _default_cortex_db()
    store_root = files_root or _default_files_root()
    jsonl_root = transcripts_root or _default_transcripts_root()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        pragma = conn.execute("PRAGMA table_info(session_journals)").fetchall()
        has_codec = any(row[1] == "verbatim_codec" for row in pragma)
        select = (
            "SELECT session_id, conversation_uuid, file_path, verbatim_bytes"
            + (", verbatim_codec" if has_codec else "")
            + " FROM session_journals "
            "WHERE conversation_uuid IS NOT NULL AND file_path IS NOT NULL"
        )
        rows = conn.execute(select).fetchall()
    finally:
        conn.close()

    checked = missing_jsonl = missing_md = 0
    legacy_parity_diff = diff_rows = 0
    prefix_extend_rows = assistant_fill_rows = jsonl_edited_after_seal = 0

    for row in rows:
        uuid = str(row["conversation_uuid"])
        jsonl_path = jsonl_root / uuid / f"{uuid}.jsonl"
        if not jsonl_path.is_file():
            missing_jsonl += 1
            continue
        md_path = store_root / str(row["file_path"])
        if not md_path.is_file():
            missing_md += 1
            continue

        session_id = str(row["session_id"])
        full_md = md_path.read_text(encoding="utf-8")
        codec = row["verbatim_codec"] if has_codec else None
        if codec == "messages-v1":
            sealed_prefix = split_verbatim_layer(full_md)
        else:
            sealed_prefix = split_verbatim_layer(
                full_md, verbatim_bytes=journal_verbatim_bytes(row)
            )

        legacy = _legacy_assemble(jsonl_path, session_id)
        envelope = extract_turns_from_jsonl(
            jsonl_path, tools="marker", session_id=session_id
        )
        rendered, _ = render_verbatim_md(envelope, session_id, assistant_label)

        checked += 1
        if rendered != legacy:
            legacy_parity_diff += 1
            diff_rows += 1
            continue

        # rendered == legacy: classify sealed drift (incl. jsonl_edited_after_seal).
        reason = sealed_roundtrip_holds(
            sealed=sealed_prefix, rendered=rendered, legacy=legacy
        )
        if reason is None:
            continue
        if reason == "prefix_extend":
            prefix_extend_rows += 1
            continue
        if reason == "assistant_fill":
            assistant_fill_rows += 1
            continue
        if reason == "jsonl_edited_after_seal":
            jsonl_edited_after_seal += 1
            continue
        diff_rows += 1

    return CorpusRoundtripReport(
        checked=checked,
        missing_jsonl=missing_jsonl,
        missing_md=missing_md,
        legacy_parity_diff=legacy_parity_diff,
        diff_rows=diff_rows,
        prefix_extend_rows=prefix_extend_rows,
        assistant_fill_rows=assistant_fill_rows,
        jsonl_edited_after_seal=jsonl_edited_after_seal,
    )


__all__ = [
    "CorpusRoundtripReport",
    "check_corpus_roundtrip",
    "sealed_roundtrip_holds",
]
