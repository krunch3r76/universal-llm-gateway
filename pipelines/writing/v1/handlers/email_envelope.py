"""Deterministic email envelope polish for correspondence channel."""

from __future__ import annotations

import re
from typing import Any

_DIGIT_DASH = re.compile(r"(\d)\s*[\u2013\u2014]\s*(\d)")
_SUBJECT = re.compile(r"(?m)^Subject\s*:", re.I)
_TO = re.compile(r"(?m)^To\s*:", re.I)
_SUBJECT_OR_RE_LINE = re.compile(r"(?m)^(?:Subject|Re)\s*:\s*(.+)$", re.I)
_MATTER_SPAN = re.compile(
    r"\b(?:Assessment Appeals?|Appeal|Case|Matter|Application|Account|Claim)\s+"
    r"(?:Nos?\.?\s*)?[0-9A-Z][0-9A-Z.\s,&-]*?[0-9A-Z](?=,|\s+APN|\s+for|\s+regarding|\s+on|\.|$)",
    re.I,
)
_APN_SPAN = re.compile(r"\bAPN\s+[0-9-]+", re.I)
_HEARING_PHRASE = re.compile(r"\b(?:[A-Z][a-z]+\s+){0,2}Hearing\b")
_DATE_SPAN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s*\d{4}\b",
    re.I,
)
_FORMAL_APPOSITIVE = re.compile(
    r"(?m)^(\s*)I,\s+[^,\n]{2,80},\s+am\s+(submitting|attaching|writing|requesting|enclosing)\b",
    re.I,
)
_FORMAL_APPOSITIVE_BODY = re.compile(
    r"(?<=\n\n)I,\s+[^,\n]{2,80},\s+am\s+(submitting|attaching|writing|requesting|enclosing)\b",
    re.I,
)
_DECLARATION_OPEN = re.compile(
    r"\bI,\s+[^,\n]{2,80},\s+am\s+(?:submitting|attaching|writing|requesting|enclosing|declare)\b",
    re.I,
)
_TOKEN = re.compile(r"[a-z0-9]+")


def normalize_email_dashes(text: str) -> str:
    """Replace unicode dashes; digit ranges become 'through'."""
    text = _DIGIT_DASH.sub(r"\1 through \2", text)
    return text.replace("\u2014", "-").replace("\u2013", "-")


def derive_email_subject(intake: dict[str, Any]) -> str:
    """Build a subject only from intake subject field or matched spans in intake text."""
    explicit = str(intake.get("subject") or "").strip()
    if explicit:
        return normalize_email_dashes(explicit)
    blob = " ".join(
        [
            str(intake.get("objective") or ""),
            *[
                str(note)
                for note in (intake.get("verbatim_notes") or [])
                if str(note).strip()
            ],
        ]
    )
    header = _SUBJECT_OR_RE_LINE.search(blob)
    if header:
        return normalize_email_dashes(header.group(1).strip())
    parts: list[str] = []
    matter = _MATTER_SPAN.search(blob)
    if matter:
        parts.append(normalize_email_dashes(matter.group(0).strip()))
    apn = _APN_SPAN.search(blob)
    if apn:
        parts.append(normalize_email_dashes(apn.group(0).strip()))
    hearing_m = _HEARING_PHRASE.search(blob)
    if hearing_m:
        hearing_text = re.sub(r"\s+", " ", hearing_m.group(0).strip())
        date_m = _DATE_SPAN.search(blob)
        if date_m:
            parts.append(
                normalize_email_dashes(
                    f"{hearing_text} on {date_m.group(0).strip()}"
                )
            )
        else:
            parts.append(normalize_email_dashes(hearing_text))
    if parts:
        return ", ".join(parts)
    return ""


def has_email_subject(text: str) -> bool:
    """Return True when the draft contains a Subject: header line."""
    return bool(_SUBJECT.search(text))


def rewrite_email_opening(text: str) -> str:
    """Rewrite formal or impersonal email openings into plain first person."""
    if not _SUBJECT.search(text) and re.search(r"(?m)^Re\s*:\s*\S", text, re.I):
        text = re.sub(r"(?m)^Re\s*:\s*", "Subject: ", text, count=1)
    text = re.sub(
        r"(?i)\bThis correspondence serves to submit\b",
        "I am submitting",
        text,
    )
    text = re.sub(
        r"(?i)\bThis correspondence serves to\b",
        "I am writing to",
        text,
    )
    text = _FORMAL_APPOSITIVE.sub(r"\1I am \2", text)
    text = _FORMAL_APPOSITIVE_BODY.sub(r"I am \1", text)
    return text


def _signer_present(text: str, signer: str) -> bool:
    if not signer.strip():
        return True
    return bool(re.search(rf"(?m)^\s*{re.escape(signer.strip())}\s*$", text, re.I))


def _normalize_signer_line(body: str, signer: str) -> str:
    """Replace a short-name closing line with the full signer when matched."""
    tokens = signer.strip().split()
    if len(tokens) < 2:
        return body
    short_name = f"{tokens[0]} {tokens[-1]}"
    if _signer_present(body, signer):
        return body
    pattern = re.compile(rf"(?m)^(\s*){re.escape(short_name)}\s*$", re.I)
    if pattern.search(body):
        return pattern.sub(rf"\1{signer.strip()}", body, count=1)
    return body


def ensure_email_envelope(
    text: str,
    *,
    recipient: str,
    signer: str,
    intake: dict[str, Any],
) -> str:
    """Ensure To/Subject/signer lines exist on an email draft."""
    body = text.strip()
    if not body:
        return body
    prefix: list[str] = []
    if recipient.strip() and not _TO.search(body):
        prefix.append(f"To: {recipient.strip()}")
    if not _SUBJECT.search(body):
        subject = derive_email_subject(intake)
        if subject:
            prefix.append(f"Subject: {subject}")
    if prefix:
        body = "\n\n".join(prefix) + "\n\n" + body.lstrip()
    if signer.strip():
        body = _normalize_signer_line(body, signer)
        if not _signer_present(body, signer):
            if not re.search(r"(?m)^(?:Thank you|Sincerely),?\s*$", body, re.I):
                body = body.rstrip() + "\n\nThank you,\n" + signer.strip()
            else:
                body = body.rstrip() + "\n" + signer.strip()
    return body


def polish_email_draft(
    text: str,
    *,
    recipient: str,
    signer: str,
    intake: dict[str, Any],
) -> str:
    """Rewrite opening, normalize dashes, and enforce email envelope lines."""
    normalized = normalize_email_dashes(text)
    rewritten = rewrite_email_opening(normalized)
    return ensure_email_envelope(
        rewritten, recipient=recipient, signer=signer, intake=intake
    )


def email_restate_pin_satisfied(draft_text: str, pin_excerpt: str) -> bool:
    """True when draft contains pin text or an email opening rewrite of it."""
    draft_tokens = set(_TOKEN.findall(draft_text.lower()))
    for variant in (pin_excerpt, rewrite_email_opening(pin_excerpt)):
        anchors = set(_TOKEN.findall(variant.lower()))
        if anchors and anchors <= draft_tokens:
            return True
        if variant.strip().lower() in draft_text.lower():
            return True
    return False


def email_draft_penalty(
    text: str,
    *,
    channel: str,
    signer: str,
    standing_limits: str,
    recipient: str = "",
) -> int:
    """Score penalty for email envelope/register defects (3 points each)."""
    if channel != "email":
        return 0
    penalty = 0
    if recipient.strip() and not _TO.search(text):
        penalty += 3
    if not _SUBJECT.search(text):
        penalty += 3
    if signer.strip() and not _signer_present(text, signer):
        penalty += 3
    if re.search(r"this correspondence serves", text, re.I):
        penalty += 3
    if _DECLARATION_OPEN.search(text):
        penalty += 3
    if "\u2014" in text or "\u2013" in text:
        penalty += 3
    if "first person" in standing_limits.casefold():
        if not re.search(
            r"\b(?:i am submitting|attached are my|i am attaching)\b", text, re.I
        ):
            penalty += 3
    return penalty
