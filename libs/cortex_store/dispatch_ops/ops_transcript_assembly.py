"""Transcript assembly — build a compliant `transcript_md` from a Cursor JSONL.

Implements P6 of `todo:session-close-friction-audit`. The composition is
deterministic and machine-addressable so agents can collapse the manual
JSONL → markdown step into a single dispatch call.

Output conforms to the dual-layer doctrine in `session-close.mdc`:
verbatim layer (`### User` / `### {assistant_label}` blocks) plus a
structural layer (`## Turn N` headings + a placeholder `## Session Summary`
section the caller is expected to overwrite when it issues `session_close`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_ASSISTANT_LABEL = "Assistant"
_TURN_TOPIC_MAX = 60


def _extract_user_text(content: list[dict[str, Any]]) -> str:
    """Pull the human-authored text out of a Cursor user message.

    Cursor wraps each user turn into a single `content` array that may
    contain (a) the typed message as a `text` block and (b) one or more
    `tool_result` blocks carrying responses from the prior assistant tool
    calls. Only the `text` blocks are user voice; tool results belong to
    the prior assistant turn and are dropped to keep the verbatim layer
    clean.
    """
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n\n".join(parts).strip()


def _extract_assistant_blocks(content: list[dict[str, Any]]) -> str:
    """Pull text + tool-call summaries out of an assistant message.

    Tool input JSON is intentionally NOT inlined verbatim — the doctrine
    says `trim tool JSON only`. We emit `[tool call: NAME]` markers so the
    structural shape of the assistant's reasoning is preserved without
    flooding the transcript with potentially-large tool arguments.
    """
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        elif btype == "tool_use":
            name = block.get("name", "<unknown>")
            parts.append(f"[tool call: {name}]")
    return "\n\n".join(parts).strip()


def _topic_hint(user_text: str) -> str:
    """Best-effort one-line topic for `## Turn N — <topic>`."""
    flat = " ".join(user_text.split())
    if len(flat) <= _TURN_TOPIC_MAX:
        return flat or "(no user text)"
    return flat[:_TURN_TOPIC_MAX].rstrip() + "…"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"jsonl_path {path} line {line_no}: invalid JSON ({exc})"
                ) from exc
    return records


def _op_assemble_transcript(
    jsonl_path: str | None = None,
    session_id: str | None = None,
    agent: str | None = None,
    assistant_label: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Assemble a dual-layer `transcript_md` string from a Cursor JSONL.

    Args:
        jsonl_path: absolute path to a Cursor `agent-transcripts/<uuid>/<uuid>.jsonl`
        session_id: target `{agent}-YYYY-MM-DD-HHMM`
        agent: agent slug for the H1 line (cosmetic; session_close validates the
            session_id pattern, not this field)
        assistant_label: heading label for assistant blocks; defaults to
            "Assistant". Pass e.g. "Assistant" or the agent's display name
            for the transcript heading.

    Returns:
        {"transcript_md": str, "turn_count": int, "byte_count": int}
        on success, or {"error": ..., "reason": ...} otherwise.
    """
    if not jsonl_path:
        return {"error": "jsonl_path is required", "reason": "missing_arg"}
    if not session_id:
        return {"error": "session_id is required", "reason": "missing_arg"}

    path = Path(jsonl_path).expanduser()
    if not path.is_file():
        return {
            "error": f"jsonl_path not found or not a file: {path}",
            "reason": "jsonl_missing",
        }

    try:
        records = _read_jsonl(path)
    except ValueError as exc:
        return {"error": str(exc), "reason": "jsonl_parse_error"}

    label = assistant_label or _DEFAULT_ASSISTANT_LABEL
    turns: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None

    for record in records:
        role = record.get("role")
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "user":
            user_text = _extract_user_text(content)
            if not user_text:
                # Pure tool_result follow-ups attach to the in-progress turn
                # so we don't open an empty `### User` block.
                continue
            if current is not None:
                turns.append(
                    {
                        "user": "\n\n".join(current["user"]).strip(),
                        "assistant": "\n\n".join(current["assistant"]).strip(),
                    }
                )
            current = {"user": [user_text], "assistant": []}
        elif role == "assistant":
            if current is None:
                # Assistant-first record (rare); stash under a synthetic empty turn.
                current = {"user": ["(no user message)"], "assistant": []}
            asst = _extract_assistant_blocks(content)
            if asst:
                current["assistant"].append(asst)
    if current is not None:
        turns.append(
            {
                "user": "\n\n".join(current["user"]).strip(),
                "assistant": "\n\n".join(current["assistant"]).strip(),
            }
        )

    lines: list[str] = [f"# Transcript: {session_id}", ""]
    for idx, turn in enumerate(turns, start=1):
        topic = _topic_hint(turn["user"])
        lines.append(f"## Turn {idx} — {topic}")
        lines.append("")
        lines.append("### User")
        lines.append("")
        lines.append(turn["user"] or "(empty)")
        lines.append("")
        lines.append(f"### {label}")
        lines.append("")
        lines.append(turn["assistant"] or "(no assistant output)")
        lines.append("")
    lines.append("## Session Summary")
    lines.append("")
    lines.append(
        "**Decisions:** _(replace this placeholder before passing to session_close;"
        " see session-close.mdc for the required structure)_"
    )
    lines.append("")

    transcript_md = "\n".join(lines)
    return {
        "transcript_md": transcript_md,
        "turn_count": len(turns),
        "byte_count": len(transcript_md.encode("utf-8")),
        "agent": agent,
    }


__all__ = ["_op_assemble_transcript"]
