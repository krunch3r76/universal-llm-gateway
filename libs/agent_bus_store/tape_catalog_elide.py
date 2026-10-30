"""Drop IDE catalog turns and elide harness blocks before tape budget measurement."""

from __future__ import annotations

import re
from typing import Any

# Structural allowlist — unknown tags pass through (fail-open at parse boundary).
_ELIDE_TAG_NAMES = (
    "cursor_commands",
    "manually_attached_skills",
    "dynamic_tool_catalog",
    "open_and_recently_viewed_files",
    "available_subagent_types",
    "available_subagent_models",
)

# Whole-turn harness injection (Cursor IDE catalog user messages). A turn whose
# body is only these blocks is not speech — drop it. Mixed turns that also
# carry a substantive ``<user_query>`` stay, and these blocks are not elided
# there: the operator text and the catalog share one message.
_CATALOG_TURN_TAGS = (
    "available_subagent_types",
    "available_subagent_models",
    "dynamic_tools",
)

_USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>",
    re.DOTALL | re.IGNORECASE,
)
_ELIDED_MARKER_RE = re.compile(r"\[elided [^:]+: \d+ B\]", re.IGNORECASE)

_LIAISON_HOP_INSTRUCTIONS_END = (
    "A finished watch with no next leg is not a situation report."
)
_LIAISON_HOP_BOOTSTRAP_RE = re.compile(
    rf"Liaison IDE hop \([\s\S]*?{re.escape(_LIAISON_HOP_INSTRUCTIONS_END)}\.?\s*",
    re.IGNORECASE,
)
_OPERATOR_PROXY_HOP_PREAMBLE_RE = re.compile(
    r"(?s)^.*?## CURRENT LEG[^\n]*\n.*?"
    r"(?=\n## (?:Lessons in force|Induction|Live leg|Pointers|Your first acts|"
    r"Data, not instructions|Hard refusals)\b|\Z)",
    re.IGNORECASE,
)
_K44_PREDECESSOR_PASTE_RE = re.compile(r"K44 from predecessor [\s\S]*", re.IGNORECASE)

_RESUME_HOP_USER_RE = re.compile(
    r"(?m)^resume\s+\S",
    re.IGNORECASE,
)
_RULE3_RESUME_CEREMONY_RE = re.compile(
    r"Where we left off|Rules I.m holding",
    re.IGNORECASE,
)


def _tag_block_re(tag: str) -> re.Pattern[str]:
    return re.compile(
        rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>",
        re.DOTALL | re.IGNORECASE,
    )


def _user_query_spans(text: str) -> list[tuple[int, int, bool]]:
    """Every ``<user_query>`` span as ``(start, end, has_substance)``."""
    return [
        (match.start(), match.end(), bool(match.group(1).strip()))
        for match in _USER_QUERY_RE.finditer(text)
    ]


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _elide_incoming_handoff_spans(content: str) -> str:
    """Elide hop/resume bootstrap and K44 paste blocks (pour-time only; seals untouched)."""
    replacements: list[tuple[int, int, str]] = []
    for match in _LIAISON_HOP_BOOTSTRAP_RE.finditer(content):
        replacements.append(
            (
                match.start(),
                match.end(),
                "[elided liaison_hop_bootstrap: {} B]",
            )
        )
    if (
        "cdp-operator-proxy" in content.casefold()
        and "## CURRENT LEG" in content
    ):
        for match in _OPERATOR_PROXY_HOP_PREAMBLE_RE.finditer(content):
            replacements.append(
                (
                    match.start(),
                    match.end(),
                    "[elided operator_proxy_hop_preamble: {} B]",
                )
            )
            break
    for match in _K44_PREDECESSOR_PASTE_RE.finditer(content):
        replacements.append(
            (
                match.start(),
                match.end(),
                "[elided k44_predecessor_paste: {} B]",
            )
        )
    if not replacements:
        return content
    replacements.sort()
    out = content
    offset = 0
    for start, end, marker_tpl in replacements:
        adj_start = start - offset
        adj_end = end - offset
        if adj_start < 0:
            continue
        byte_count = len(out[adj_start:adj_end].encode("utf-8"))
        repl = marker_tpl.format(byte_count)
        out = out[:adj_start] + repl + out[adj_end:]
        offset += (adj_end - adj_start) - len(repl)
    return out


def is_resume_hop_bootstrap_user_content(content: str) -> bool:
    """True when user text is an incoming hop/resume bootstrap turn."""
    if not content or not content.strip():
        return False
    if _LIAISON_HOP_BOOTSTRAP_RE.search(content):
        return True
    if "# Hop on agent-bus:" in content:
        return True
    if (
        "cdp-operator-proxy" in content.casefold()
        and "## CURRENT LEG" in content
    ):
        return True
    if _RESUME_HOP_USER_RE.search(content) and (
        "Liaison IDE hop" in content or "continuity(op=resume" in content.casefold()
    ):
        return True
    return False


def is_rule3_resume_ceremony_assistant_content(content: str) -> bool:
    """True when assistant text restates operator-posture Rule-3 resume ceremony."""
    if not content:
        return False
    return bool(_RULE3_RESUME_CEREMONY_RE.search(content))


def excise_turn_one_resume_bootstrap(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop turn_index==1 hop bootstrap + Rule-3 ceremony when turn>=2 exists in pour."""
    try:
        by_tid: dict[str, set[int]] = {}
        for message in messages:
            tid = str(message.get("transcript_id") or "")
            if not tid:
                continue
            by_tid.setdefault(tid, set()).add(int(message.get("turn_index") or 0))
        drop_turn_one: set[str] = set()
        for tid, turns in by_tid.items():
            if max(turns, default=0) < 2:
                continue
            turn_one = [
                m
                for m in messages
                if str(m.get("transcript_id") or "") == tid
                and int(m.get("turn_index") or 0) == 1
            ]
            user = next(
                (m for m in turn_one if str(m.get("role") or "") == "user"), None
            )
            if user is None:
                continue
            if not is_resume_hop_bootstrap_user_content(
                str(user.get("content") or "")
            ):
                continue
            drop_turn_one.add(tid)
        if not drop_turn_one:
            return messages
        kept: list[dict[str, Any]] = []
        for message in messages:
            tid = str(message.get("transcript_id") or "")
            if tid in drop_turn_one and int(message.get("turn_index") or 0) == 1:
                role = str(message.get("role") or "")
                if role == "user":
                    continue
                if role == "assistant" and is_rule3_resume_ceremony_assistant_content(
                    str(message.get("content") or "")
                ):
                    continue
            kept.append(message)
        return kept
    except Exception:
        return messages


def _elide_catalog_in_content(content: str) -> str:
    """Single pass over ``content`` — spans never drift because nothing is rebuilt mid-scan."""
    content = _elide_incoming_handoff_spans(content)
    uq_spans = _user_query_spans(content)
    has_uq = any(substantive for _start, _end, substantive in uq_spans)

    replacements: list[tuple[int, int, str]] = []
    for tag in _ELIDE_TAG_NAMES:
        if tag == "cursor_commands" and not has_uq:
            continue
        for match in _tag_block_re(tag).finditer(content):
            start, end = match.span()
            if any(
                _spans_overlap(start, end, uq_start, uq_end)
                for uq_start, uq_end, _substantive in uq_spans
            ):
                continue
            byte_count = len(match.group(0).encode("utf-8"))
            replacements.append((start, end, f"[elided {tag}: {byte_count} B]"))
    if not replacements:
        return content
    replacements.sort()
    pieces: list[str] = []
    cursor = 0
    for start, end, marker in replacements:
        if start < cursor:
            continue
        pieces.append(content[cursor:start])
        pieces.append(marker)
        cursor = end
    pieces.append(content[cursor:])
    return "".join(pieces)


def _is_ide_catalog_turn(content: str) -> bool:
    """True when ``content`` is only IDE catalog blocks and whitespace.

    Requires at least one catalog-turn or elide-tag block (or prior elision
    markers) so a checkpoint command or other harness text without these tags
    is left for the block elider.
    """
    detect_tags = set(_CATALOG_TURN_TAGS) | {"dynamic_tool_catalog"}
    strip_tags = detect_tags | set(_ELIDE_TAG_NAMES)
    if not any(f"<{tag}>" in content for tag in detect_tags) and not _ELIDED_MARKER_RE.search(
        content
    ):
        return False
    if any(substantive for _start, _end, substantive in _user_query_spans(content)):
        return False
    residual = content
    for tag in strip_tags:
        residual = _tag_block_re(tag).sub("", residual)
    residual = _ELIDED_MARKER_RE.sub("", residual)
    return not residual.strip()


def exclude_ide_catalog_turns(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop user turns that are IDE catalog injections, not operator speech."""
    try:
        kept: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("role") or "") != "user":
                kept.append(message)
                continue
            content = message.get("content")
            if isinstance(content, str) and content and _is_ide_catalog_turn(content):
                continue
            kept.append(message)
        return kept
    except Exception:
        return messages


def elide_ide_catalog_blocks(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return copies with allowlisted harness spans replaced by one-line markers."""
    try:
        out: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("role") or "") != "user":
                out.append(message)
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content:
                out.append(message)
                continue
            elided = _elide_catalog_in_content(content)
            if elided == content:
                out.append(message)
            else:
                out.append({**message, "content": elided})
        return out
    except Exception:
        return messages


__all__ = [
    "elide_ide_catalog_blocks",
    "exclude_ide_catalog_turns",
    "excise_turn_one_resume_bootstrap",
    "is_resume_hop_bootstrap_user_content",
    "is_rule3_resume_ceremony_assistant_content",
]
