"""Extract operator-facing one-liner topic from CDP generate prompt bodies."""

from __future__ import annotations

_TOPIC_MAX_CHARS = 160
_PREFERRED_TOPIC_PREFIXES = ("so_what:", "ulg_gain:")
_PURPOSE_TAGS = frozenset({"ask", "operator-proxy", "mission", "review", "produce"})


def extract_cdp_dispatch_topic(body: str | None) -> str | None:
    """One-line dispatch topic from prompt prose, capped at 160 chars.

    Prefers ``so_what:`` / ``ulg_gain:`` when present; otherwise the first
    non-empty line. Returns ``None`` for purpose tags, staged-prompt lines, and
    skill-invocation lines.
    """
    if not body:
        return None
    preferred: str | None = None
    first: str | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if first is None:
            first = line
        lower = line.lower()
        if lower in _PURPOSE_TAGS:
            continue
        if lower.startswith("cdp generate"):
            continue
        if line.startswith("/"):
            continue
        if lower.startswith("use the "):
            continue
        for prefix in _PREFERRED_TOPIC_PREFIXES:
            if lower.startswith(prefix):
                value = line[len(prefix) :].strip()
                preferred = value or line
                break
        if preferred is not None:
            break
    raw_topic = preferred or first
    if not raw_topic:
        return None
    lower = raw_topic.lower()
    if lower in _PURPOSE_TAGS:
        return None
    if lower.startswith("cdp generate") or raw_topic.startswith("/"):
        return None
    if lower.startswith("use the "):
        return None
    if len(raw_topic) <= _TOPIC_MAX_CHARS:
        return raw_topic
    clipped = raw_topic[:_TOPIC_MAX_CHARS]
    head, _, _ = clipped.rpartition(" ")
    return f"{head or clipped}…"
