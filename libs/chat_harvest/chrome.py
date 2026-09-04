"""Shared Cowork/CDP harvest chrome detection and stripping."""

from __future__ import annotations

import re

# Tool-badge vocabulary observed in Cowork scrape UI (incl. streaming mid-reply).
_TOOL_BADGE_SEGMENT = (
    r"(searched the web|used toys integration|used a skill|used \d+ skills?|loaded tools)"
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


def strip_chrome(text: str) -> str:
    """Drop Cowork scrape chrome; keep model prose."""
    lines = text.split("\n")
    if lines and _RESPONDED_LABEL_RE.match(lines[0].strip()):
        lines = lines[1:]
    lines = [
        line for line in lines if not TOOL_BADGE_LINE_RE.match(line.strip())
    ]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and _TRAILING_TIMESTAMP_RE.match(lines[-1].strip()):
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


def is_relay_envelope_subject(subject: str) -> bool:
    """True when the bus subject is a CDP on-behalf relay envelope."""
    return bool(RELAY_ENVELOPE_SUBJECT_RE.match(str(subject or "").strip()))


def is_failed_relay_envelope_subject(subject: str) -> bool:
    """True for FAILED/UNVERIFIED CDP envelope subjects (never proof-complete)."""
    return bool(_CDP_FAILED_UNVERIFIED_SUBJECT_RE.match(str(subject or "").strip()))
