"""Dispatch topic extraction for SDK nest-tree board paint (G5.1).

Text fallback for GIW and non-conductor admits; structural conductor mission
threading for Stargate prepare when ``packet_kind=conductor``.
"""

from __future__ import annotations

import re

from implement_admission.materialize import _extract_block

_TOPIC_MAX_CHARS = 160
_PREFERRED_TOPIC_PREFIXES = ("so_what:", "ulg_gain:")
_CORPUS_LINE_PREFIXES = ("intent:", "problem:")
_CONDUCTOR_SCOPE_SKIP_RE = re.compile(r"^Conductor session for\b", re.IGNORECASE)
_PACKET_KIND_FRONTMATTER_RE = re.compile(
    r"^packet_kind:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE
)


def _cap_topic(raw_topic: str) -> str:
    if len(raw_topic) <= _TOPIC_MAX_CHARS:
        return raw_topic
    clipped = raw_topic[:_TOPIC_MAX_CHARS]
    head, _, _ = clipped.rpartition(" ")
    return f"{head or clipped}…"


def _strip_frontmatter(text: str) -> str:
    """Return body with YAML frontmatter (``---`` … ``---``) removed."""
    if not text.startswith("---"):
        return text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def _is_skippable_scan_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    if stripped.startswith("<") and ">" in stripped:
        return True
    if lower.startswith("use the "):
        return True
    if lower.startswith("packet_kind:") or lower.startswith("work_key:"):
        return True
    return False


def _line_value_after_colon(line: str) -> str | None:
    if ":" not in line:
        return None
    value = line.split(":", 1)[1].strip()
    return value or None


def _extract_prefixed_lines(
    text: str,
    prefixes: tuple[str, ...],
) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _is_skippable_scan_line(line):
            continue
        lower = line.lower()
        for prefix in prefixes:
            if lower.startswith(prefix):
                value = _line_value_after_colon(line)
                if value:
                    return value
    return None


def _extract_corpus_intent(body: str) -> str | None:
    corpus = _extract_block(body, "corpus")
    if corpus is None:
        return _extract_prefixed_lines(body, _CORPUS_LINE_PREFIXES)
    return _extract_prefixed_lines(corpus, _CORPUS_LINE_PREFIXES)


def _extract_scope_prose(body: str) -> str | None:
    scope = _extract_block(body, "scope")
    if scope is None:
        return None
    for raw_line in scope.splitlines():
        line = raw_line.strip()
        if not line or _is_skippable_scan_line(line):
            continue
        if _CONDUCTOR_SCOPE_SKIP_RE.match(line):
            continue
        return line
    return None


def extract_packet_kind_from_body(text: str | None) -> str | None:
    """Return ``packet_kind:`` frontmatter when present."""
    if not text:
        return None
    match = _PACKET_KIND_FRONTMATTER_RE.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


def extract_dispatch_topic(body: str | None) -> str | None:
    """One-line operator topic from packet/message prose, capped at 160 chars.

    Skips YAML frontmatter, XML tag lines, skill-invocation lines, and
    ``packet_kind`` / ``work_key`` keys. Prefers ``so_what:`` / ``ulg_gain:``,
    then corpus ``Intent:`` / ``Problem:``, then first real ``<scope>`` prose
    that is not ``Conductor session for …``; otherwise omits.
    """
    if not body:
        return None

    stripped_body = _strip_frontmatter(body)

    preferred = _extract_prefixed_lines(stripped_body, _PREFERRED_TOPIC_PREFIXES)
    if preferred:
        return _cap_topic(preferred)

    intent = _extract_corpus_intent(stripped_body)
    if intent:
        return _cap_topic(intent)

    scope_topic = _extract_scope_prose(stripped_body)
    if scope_topic:
        return _cap_topic(scope_topic)

    return None


def conductor_mission_topic(todo_name: str) -> str:
    """Structural conductor mission topic for handle threading."""
    return _cap_topic(f"Conductor unify — {todo_name.strip()}")


def derive_conductor_topic_from_packet(packet_text: str) -> str | None:
    """Extract conductor mission from corpus ``Intent:`` (structural parse)."""
    corpus = _extract_block(packet_text, "corpus")
    if corpus is None:
        return None
    for raw_line in corpus.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("intent:"):
            value = _line_value_after_colon(line)
            if value:
                return _cap_topic(value)
    return None


def derive_handle_topic(
    *,
    packet_kind: str | None,
    packet_text: str | None,
    message_text: str | None = None,
    todo_name: str | None = None,
) -> str | None:
    """Stargate admit-path topic derivation."""
    kind = (packet_kind or "").strip().lower()
    if kind == "conductor":
        if todo_name:
            return conductor_mission_topic(todo_name)
        if packet_text:
            return derive_conductor_topic_from_packet(packet_text)
        return None
    if packet_text:
        return extract_dispatch_topic(packet_text)
    if message_text:
        return extract_dispatch_topic(message_text)
    return None
