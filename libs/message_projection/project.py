"""Project chat-transcript message rows into deterministic markdown for indexing.

The RAG indexer calls ``project_messages`` with a messages-format list and a
small meta dict before chunking. The function is pure: no I/O, no clock, and
the same inputs always return the same string. Hidden prompt tags are stripped
or collapsed to one line, and bytes the source already cut are marked
``[source-truncated]`` instead of being repaired or treated as complete.
"""

from __future__ import annotations

import json
import re
from typing import Any

TRUNCATION_LEN_WINDOW = (3990, 4010)
TRUNCATION_ELLIPSIS = "..."
TRUNCATION_MARKER_RE = re.compile(
    r"(?:\[\s*\.\.\.\s*\d+\s+chars?\s+truncated\s*\.\.\.\s*\]"
    r"|…\s*\[\s*truncated\s+\d+\s+chars?\s*\]\s*…"
    r"|\.\.\.\s*\[\s*truncated\s+\d+\s+chars?\s*\])",
    re.IGNORECASE,
)
BACKGROUND_TASK_LABEL_RE = re.compile(
    r'(Background task ")(.+?)(…|\.\.\.)("(?:\s*\([^)]+\))?\s*finished:)'
)
USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)
SYSTEM_REMINDER_RE = re.compile(r"<system_reminder>.*?</system_reminder>", re.DOTALL)
MEMORY_CONTEXT_RE = re.compile(r"<memory_context>.*?</memory_context>", re.DOTALL)
TIMESTAMP_EL_RE = re.compile(r"<timestamp>.*?</timestamp>", re.DOTALL)
TIMESTAMP_INNER_RE = re.compile(r"<timestamp>(.*?)</timestamp>", re.DOTALL)
GROK_HIDDEN_RE = re.compile(r"\[GROK_BOT_HIDDEN_PROMPT\]\s*(?:\[[^\]]*\])?")
TOOL_CALL_PLACEHOLDER_RE = re.compile(r"\[tool call:\s*[^\]]+\]")
ZONE_SUFFIX_RE = re.compile(r"\s*\((?:UTC-7|PDT|PST)\)\s*$")
SOURCE_TRUNCATED_LINE = "[source-truncated]"


def project_messages(messages: list[dict], meta: dict) -> str:
    """Render ``messages`` as markdown turns using ``meta`` for fallback timestamps.

    Each input row is ``{role, content}`` or ReadTranscript ``{role, message:{content}}``.
    Block-shaped ``content`` lists and tool_result text blocks are copied in full
    inside fences. Thinking blocks are omitted. A ``[tool call: NAME]`` stub with
    no tool input is kept and marked ``[source-truncated]``. Hidden tags collapse
    to one line. No I/O. The same ``messages`` and ``meta`` always yield the same
    string, including a second call on the same objects.
    """
    if not messages:
        return ""
    observed_at = meta.get("observed_at") if isinstance(meta, dict) else None
    sections: list[str] = []
    for index, raw in enumerate(messages, start=1):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role", "unknown"))
        turn_n = _turn_number(raw, index)
        content = _message_content(raw)
        ts = _resolve_timestamp(content, raw, observed_at)
        body = _render_body(content)
        sections.append(f"## Turn {turn_n} - {ts} - {role}\n\n{body}".rstrip())
    return "\n\n".join(sections).rstrip()


def _turn_number(msg: dict[str, Any], position: int) -> int:
    turn_index = msg.get("turn_index")
    if isinstance(turn_index, int):
        return turn_index
    return position


def _message_content(msg: dict[str, Any]) -> str | list[Any]:
    nested = msg.get("message")
    if isinstance(nested, dict) and "content" in nested:
        return nested["content"]
    content = msg.get("content")
    if content is None:
        return ""
    return content


def _resolve_timestamp(
    content: str | list[Any],
    msg: dict[str, Any],
    observed_at: Any,
) -> str:
    inner = _timestamp_from_content(content)
    if inner is None:
        for key in ("timestamp", "ts"):
            val = msg.get(key)
            if isinstance(val, str) and val.strip():
                inner = val.strip()
                break
    if inner is None and isinstance(observed_at, str) and observed_at.strip():
        inner = observed_at.strip()
    if inner is None:
        inner = "unknown"
    inner = ZONE_SUFFIX_RE.sub("", inner).rstrip()
    if "PT" not in inner:
        inner = f"{inner} PT"
    return inner


def _timestamp_from_content(content: str | list[Any]) -> str | None:
    texts = _collect_raw_texts(content)
    for text in texts:
        match = TIMESTAMP_INNER_RE.search(text)
        if match:
            return match.group(1).strip()
    return None


def _collect_raw_texts(content: str | list[Any]) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                out.append(text)
    return out


def _render_body(content: str | list[Any]) -> str:
    if isinstance(content, str):
        body = _transform_text(content)
        if _payload_is_truncated(body):
            body = _append_source_truncated(body)
        return body.rstrip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    any_truncated = False
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking":
            continue
        if kind == "text":
            text = block.get("text", "")
            if not isinstance(text, str):
                continue
            transformed = _transform_text(text)
            if transformed:
                parts.append(transformed)
            if _payload_is_truncated(text):
                any_truncated = True
        elif kind == "tool_use":
            segment, truncated = _format_tool_use(block)
            if segment:
                parts.append(segment)
            any_truncated = any_truncated or truncated
        elif kind == "tool_result":
            segment, truncated = _format_tool_result(block)
            if segment:
                parts.append(segment)
            any_truncated = any_truncated or truncated
    body = "\n\n".join(parts)
    if any_truncated:
        body = _append_source_truncated(body)
    return body.rstrip()


def _transform_text(text: str) -> str:
    out = USER_QUERY_RE.sub(lambda m: m.group(1), text)
    out = TIMESTAMP_EL_RE.sub("", out)
    out = SYSTEM_REMINDER_RE.sub("[system-reminder]", out)
    out = MEMORY_CONTEXT_RE.sub("[memory-context]", out)
    out = GROK_HIDDEN_RE.sub("", out)
    if _hidden_prompt_is_whole_message(text):
        out = " ".join(out.split())
    else:
        out = out.strip()
    if not out:
        return "[hidden-prompt]"
    return out


def _hidden_prompt_is_whole_message(text: str) -> bool:
    """True when the row is a hidden prompt, so its residue must be one line."""
    core = text.strip()
    wrapped = USER_QUERY_RE.fullmatch(core)
    if wrapped:
        core = wrapped.group(1).strip()
    return core.startswith("[GROK_BOT_HIDDEN_PROMPT]") or core.startswith(
        "[SAND_HIDDEN_PROMPT]"
    )


def _format_tool_use(block: dict[str, Any]) -> tuple[str, bool]:
    name = block.get("name", "")
    if not isinstance(name, str):
        name = str(name)
    payload, truncated = _serialize_tool_payload(block.get("input"))
    fence = _fence_for(payload)
    segment = f"tool_use: {name}\n{fence}\n{payload}\n{fence}"
    return segment, truncated


def _format_tool_result(block: dict[str, Any]) -> tuple[str, bool]:
    name = block.get("name", "")
    if not isinstance(name, str):
        name = str(name)
    raw = block.get("result")
    if raw is None:
        raw = block.get("content")
    if _is_content_block_list(raw):
        payload, truncated = _render_content_block_list(raw)
    else:
        payload, truncated = _serialize_tool_payload(raw)
    fence = _fence_for(payload)
    segment = f"tool_result: {name}\n{fence}\n{payload}\n{fence}"
    return segment, truncated


def _is_content_block_list(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(
        isinstance(item, dict) and isinstance(item.get("type"), str) for item in value
    )


def _render_content_block_list(blocks: list[Any]) -> tuple[str, bool]:
    """Join tool_result text blocks verbatim so the fence holds the full text."""
    parts: list[str] = []
    truncated = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking":
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if not isinstance(text, str):
                text = str(text)
            parts.append(text)
            truncated = truncated or _payload_is_truncated(text)
            continue
        payload, flag = _serialize_tool_payload(block)
        parts.append(payload)
        truncated = truncated or flag
    return "\n\n".join(parts), truncated


def _serialize_tool_payload(value: Any) -> tuple[str, bool]:
    if isinstance(value, (dict, list)):
        payload = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)
    elif value is None:
        payload = "null"
    else:
        payload = str(value)
    return payload, _payload_is_truncated(payload)


def _fence_for(payload: str) -> str:
    longest = 0
    run = 0
    for ch in payload:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    length = max(3, longest + 1)
    return "`" * length


def _ellipsis_in_middle(text: str) -> bool:
    lo, hi = TRUNCATION_LEN_WINDOW
    if not (lo <= len(text) <= hi):
        return False
    idx = text.find(TRUNCATION_ELLIPSIS)
    if idx < 0:
        return False
    return len(text) // 4 <= idx <= (3 * len(text)) // 4


def _payload_is_truncated(text: str) -> bool:
    if TRUNCATION_MARKER_RE.search(text):
        return True
    if TOOL_CALL_PLACEHOLDER_RE.search(text):
        return True
    if BACKGROUND_TASK_LABEL_RE.search(text):
        return True
    if _ellipsis_in_middle(text):
        return True
    return False


def _append_source_truncated(body: str) -> str:
    trimmed = body.rstrip()
    if trimmed.endswith(SOURCE_TRUNCATED_LINE):
        return trimmed
    if trimmed:
        return f"{trimmed}\n{SOURCE_TRUNCATED_LINE}"
    return SOURCE_TRUNCATED_LINE
