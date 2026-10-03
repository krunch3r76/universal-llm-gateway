"""Shared Cowork/CDP harvest chrome detection and stripping.

``strip_chrome`` and ``is_chrome_only`` drop phrase-list tool badges.
``is_tool_status_body`` is the shape gate the DOM wait ORs in, so a badge
line outside that phrase list still holds the harvest. Callers are the
chat reply wait and the chrome unit tests. No events or I/O.
"""

from __future__ import annotations

import re

# Tool-badge vocabulary observed in Cowork scrape UI (incl. streaming mid-reply).
# "Ran a command, loaded tools" is a progress badge, not the reply (12887 turn 27).
_TOOL_BADGE_SEGMENT = (
    r"(searched the web|used toys integration|used a skill|used \d+ skills?|"
    r"used \d+ tools?|loaded tools|loaded a skill|loaded \d+ skills?|"
    r"ran a command|ran \d+ commands?)"
)
TOOL_BADGE_LINE_RE = re.compile(
    rf"^{_TOOL_BADGE_SEGMENT}(,\s*{_TOOL_BADGE_SEGMENT})*\.?$",
    re.I,
)

_RESPONDED_LABEL_RE = re.compile(r"^claude responded:.*$", re.I)
_TRAILING_TIMESTAMP_RE = re.compile(
    r"^(just now|\d+\s+(second|minute|hour|day)s?\s+ago)\.?$",
    re.I,
)

# Agent-bus subjects for on-behalf CDP generate envelopes.
RELAY_ENVELOPE_SUBJECT_RE = re.compile(
    r"^cdp (?:reply|FAILED|UNVERIFIED) — ",
    re.I,
)
_CDP_FAILED_UNVERIFIED_SUBJECT_RE = re.compile(
    r"^cdp (?:FAILED|UNVERIFIED) — ",
    re.I,
)

_CDP_ENVELOPE_HEADER_RE = re.compile(
    r"^#\s*CDP generate (?:result|FAILED|UNVERIFIED)\b",
    re.I,
)
_ENVELOPE_METADATA_LINE_RE = re.compile(
    r"^-\s+(execution_id|satellite_execution_id|substrate|cost_source|"
    r"archive_uri|content_proof_uri|content_proof_sha256|stall_stage|error|"
    r"body_len|chat_url|deliverable_present_unproven|recovery|reason):\s",
    re.I,
)
_STATUS_FAILED_LINE_RE = re.compile(r"^status:failed\b", re.I)

# Fixed irregular past verbs named by the badge-only harvest archive.
# The period rule and the idle change key do not read this set.
_IRREGULAR_PAST_VERBS = frozenset(
    {
        "ran",
        "read",
        "wrote",
        "made",
        "found",
        "took",
        "saw",
        "got",
        "sent",
        "built",
    }
)
# G4 left pronouns other than ``it`` unbound. ``Fixed it, …`` must complete.
_TOOL_STATUS_PRONOUNS = frozenset({"it"})


_PUA_MIN = 0xE000
_PUA_MAX = 0xF8FF


def _is_private_use_char(ch: str) -> bool:
    return _PUA_MIN <= ord(ch) <= _PUA_MAX


def _is_badge_line(line: str) -> bool:
    return bool(TOOL_BADGE_LINE_RE.match(line.strip()))


def _is_symbol_only_line(line: str) -> bool:
    """Cowork icon glyphs (BMP private-use) between tool badges are not prose.

    ASCII punctuation used in code (``}``, ``---``) must survive strip_chrome
    (a:37508 B2). Lone non-PUA glyphs next to a badge still drop via
    ``_is_lone_glyph_adjacent_to_badge``.
    """
    stripped = line.strip()
    if not stripped:
        return False
    return all(_is_private_use_char(ch) or ch.isspace() for ch in stripped)


def _is_lone_glyph_adjacent_to_badge(lines: list[str], index: int) -> bool:
    """True for single-glyph lines sandwiched next to tool-badge rows."""
    stripped = lines[index].strip()
    if len(stripped) != 1:
        return False
    if index > 0 and _is_badge_line(lines[index - 1]):
        return True
    return index + 1 < len(lines) and _is_badge_line(lines[index + 1])


def strip_chrome(text: str) -> str:
    """Drop Cowork scrape chrome; keep model prose."""
    lines = text.split("\n")
    if lines and _RESPONDED_LABEL_RE.match(lines[0].strip()):
        lines = lines[1:]
    lines = [
        line
        for index, line in enumerate(lines)
        if not _is_badge_line(line)
        and not _is_symbol_only_line(line)
        and not _is_lone_glyph_adjacent_to_badge(lines, index)
    ]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and (
        not lines[-1].strip() or _TRAILING_TIMESTAMP_RE.match(lines[-1].strip())
    ):
        lines.pop()
    return "\n".join(lines).strip()


def is_prompt_echo(body: str) -> bool:
    """True when harvested text is Cowork user-turn chrome."""
    return (body or "").lstrip().lower().startswith("you said:")


def _is_metadata_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return True
    if stripped.startswith("/") and " " not in stripped.rstrip("/"):
        return True
    if _CDP_ENVELOPE_HEADER_RE.match(stripped):
        return True
    if _ENVELOPE_METADATA_LINE_RE.match(stripped):
        return True
    if _STATUS_FAILED_LINE_RE.match(stripped):
        return True
    if stripped.startswith("status:failed"):
        return True
    if stripped.startswith("#"):
        return True
    if TOOL_BADGE_LINE_RE.match(stripped):
        return True
    if _is_symbol_only_line(stripped):
        return True
    return False


def is_chrome_only(body: str) -> bool:
    """True when body is skill-chip / manifest / envelope chrome without assistant prose."""
    text = (body or "").strip()
    if not text:
        return False
    if is_prompt_echo(text):
        return True
    stripped = strip_chrome(text)
    if not stripped:
        return True
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if not lines:
        return True
    return all(_is_metadata_line(line) for line in lines)


def substantive_reply_body(body: str) -> str:
    """Substantive assistant prose after chrome and CDP envelope metadata."""
    text = strip_chrome(body or "")
    if not text or is_prompt_echo(text):
        return ""
    if is_chrome_only(body):
        return ""
    prose: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _is_metadata_line(stripped):
            continue
        prose.append(line)
    return "\n".join(prose).strip()


def _period_stripped(line: str) -> str:
    """Drop one trailing ASCII period. ``·`` is left in place."""
    text = line.strip()
    if text.endswith("."):
        return text[:-1].rstrip()
    return text


def _is_tool_status_segment(segment: str) -> bool:
    words = segment.split()
    if not 2 <= len(words) <= 4:
        return False
    verb = words[0].lower()
    irregular = verb in _IRREGULAR_PAST_VERBS
    regular = len(verb) > 2 and verb.endswith("ed")
    if not irregular and not regular:
        return False
    return not any(word.lower() in _TOOL_STATUS_PRONOUNS for word in words[1:])


def _is_tool_status_line(line: str) -> bool:
    text = _period_stripped(line)
    if not text or any(ch in ".?!" for ch in text):
        return False
    segments = [part.strip() for part in text.split(",")]
    if not segments or any(not part for part in segments):
        return False
    return all(_is_tool_status_segment(part) for part in segments)


def _adjacent_symbol_line(lines: list[str], index: int) -> bool:
    if index > 0 and _is_symbol_only_line(lines[index - 1]):
        return True
    return index + 1 < len(lines) and _is_symbol_only_line(lines[index + 1])


def is_tool_status_body(body: str) -> bool:
    """True when every non-chrome line is a Cowork tool-status scrape.

    The DOM wait calls this from ``_badge_only_body`` only. Phrase-list
    badges stay on ``is_chrome_only`` because ``strip_chrome`` already
    drops them. A one-segment line counts only when that line repeats or
    an icon-glyph line sits beside it on the raw scrape. Empty residue
    returns False. No I/O.
    """
    raw = (body or "").split("\n")
    start = 1 if raw and _RESPONDED_LABEL_RE.match(raw[0].strip()) else 0
    kept: list[tuple[int, str]] = []
    for index, line in enumerate(raw):
        if index < start or not line.strip():
            continue
        if (
            _is_badge_line(line)
            or _is_symbol_only_line(line)
            or _is_lone_glyph_adjacent_to_badge(raw, index)
        ):
            continue
        kept.append((index, line))
    while kept and _TRAILING_TIMESTAMP_RE.match(kept[-1][1].strip()):
        kept.pop()
    if not kept or not all(_is_tool_status_line(line) for _, line in kept):
        return False
    normalized = [_period_stripped(line) for _, line in kept]
    for (index, _line), text in zip(kept, normalized, strict=True):
        if "," in text:
            continue
        repeated = normalized.count(text) >= 2
        if not repeated and not _adjacent_symbol_line(raw, index):
            return False
    return True


def badge_scrape_change_key(body: str) -> str:
    """Identity of a badge scrape for the DOM wait's idle refresh.

    Drops a leading ``Claude responded:`` line, symbol-only lines, and one
    trailing timestamp. Badge lines stay, so two different tool rows compare
    unequal and a timestamp-only tick compares equal. No I/O.
    """
    lines = (body or "").split("\n")
    if lines and _RESPONDED_LABEL_RE.match(lines[0].strip()):
        lines = lines[1:]
    kept = [line for line in lines if line.strip() and not _is_symbol_only_line(line)]
    if kept and _TRAILING_TIMESTAMP_RE.match(kept[-1].strip()):
        kept.pop()
    return "\n".join(line.strip() for line in kept if line.strip())


def is_relay_envelope_subject(subject: str) -> bool:
    """True when the bus subject is a CDP on-behalf relay envelope."""
    return bool(RELAY_ENVELOPE_SUBJECT_RE.match(str(subject or "").strip()))


def is_failed_relay_envelope_subject(subject: str) -> bool:
    """True for FAILED/UNVERIFIED CDP envelope subjects (never proof-complete)."""
    return bool(_CDP_FAILED_UNVERIFIED_SUBJECT_RE.match(str(subject or "").strip()))
