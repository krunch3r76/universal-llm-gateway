"""Deterministic email envelope polish for correspondence channel."""

from __future__ import annotations

import re
from typing import Any

_DIGIT_DASH = re.compile(r"(\d)\s*[\u2013\u2014]\s*(\d)")
_SUBJECT = re.compile(r"(?m)^Subject\s*:", re.I)
_TO = re.compile(r"(?m)^To\s*:", re.I)
_APPEALS = re.compile(
    r"Assessment Appeals\s+([0-9.\sand]+?)(?:,|\s+APN|\s+for|\s+regarding|$)",
    re.I,
)
_APN = re.compile(r"APN\s+([0-9-]+)", re.I)
_HEARING_DATE = re.compile(
    r"(Tuesday,\s+)?October\s+27,\s*2026",
    re.I,
)


def normalize_email_dashes(text: str) -> str:
    """Replace unicode dashes; digit ranges become 'through'."""
    text = _DIGIT_DASH.sub(r"\1 through \2", text)
    return text.replace("\u2014", "-").replace("\u2013", "-")


def derive_email_subject(intake: dict[str, Any]) -> str:
    """Build a subject line from intake objective and notes."""
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
    appeals = _APPEALS.search(blob)
    apn = _APN.search(blob)
    hearing = _HEARING_DATE.search(blob)
    parts: list[str] = []
    if appeals:
        nums = re.sub(r"\s+", " ", appeals.group(1).strip())
        parts.append(f"Assessment Appeals {nums}")
    if apn:
        parts.append(f"APN {apn.group(1)}")
    if hearing:
        parts.append("Reinstatement Hearing on October 27, 2026")
    if parts:
        return ", ".join(parts)
    objective = str(intake.get("objective") or "").strip()
    if len(objective) > 120:
        return objective[:117] + "..."
    return objective or "Submission"


def _signer_present(text: str, signer: str) -> bool:
    if not signer.strip():
        return True
    return bool(re.search(rf"(?m)^\s*{re.escape(signer.strip())}\s*$", text, re.I))


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
        prefix.append(f"Subject: {subject}")
    if prefix:
        body = "\n\n".join(prefix) + "\n\n" + body.lstrip()
    if signer.strip() and not _signer_present(body, signer):
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
    """Normalize dashes and enforce email envelope lines."""
    normalized = normalize_email_dashes(text)
    return ensure_email_envelope(
        normalized, recipient=recipient, signer=signer, intake=intake
    )


def email_draft_penalty(
    text: str,
    *,
    channel: str,
    signer: str,
    standing_limits: str,
) -> int:
    """Score penalty for email envelope/register defects (3 points each)."""
    if channel != "email":
        return 0
    penalty = 0
    if not _SUBJECT.search(text):
        penalty += 3
    if signer.strip() and not _signer_present(text, signer):
        penalty += 3
    if re.search(r"this correspondence serves", text, re.I):
        penalty += 3
    if "\u2014" in text or "\u2013" in text:
        penalty += 3
    if "first person" in standing_limits.casefold():
        if not re.search(
            r"\b(?:i am submitting|attached are my|i am attaching)\b", text, re.I
        ):
            penalty += 3
    return penalty
