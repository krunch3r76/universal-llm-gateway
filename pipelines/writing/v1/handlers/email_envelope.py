"""Deterministic email envelope polish for correspondence channel."""

from __future__ import annotations

import re
from typing import Any

_DIGIT_DASH = re.compile(r"(\d)\s*[\u2013\u2014]\s*(\d)")
_SUBJECT = re.compile(r"(?m)^Subject\s*:", re.I)
_TO = re.compile(r"(?m)^To\s*:", re.I)
_SUBJECT_OR_RE_LINE = re.compile(r"^\s*(?:Subject|Re)\s*:\s*(.+)$", re.I)
_FORMAL_APPOSITIVE = re.compile(
    r"(?m)^(\s*)I,\s+[^,\n]{2,80},\s+am\s+(submitting|attaching|writing|requesting|enclosing)\b",
    re.I,
)
_DECLARATION_OPEN = re.compile(
    r"\bI,\s+[^,\n]{2,80},\s+am\s+(?:submitting|attaching|writing|requesting|enclosing|declare)\b",
    re.I,
)
_TOKEN = re.compile(r"[a-z0-9]+")
_EXHIBIT_NOTE_RE = re.compile(r"^\s*(Exhibit\s+\d+)\s*:\s*(.+)$", re.I)
_RESTATE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "my",
        "of",
        "on",
        "or",
        "plus",
        "that",
        "the",
        "through",
        "to",
        "via",
        "with",
    }
)


def normalize_email_dashes(text: str) -> str:
    """Replace unicode dashes; digit ranges become 'through'."""
    text = _DIGIT_DASH.sub(r"\1 through \2", text)
    return text.replace("\u2014", "-").replace("\u2013", "-")


def derive_email_subject(intake: dict[str, Any]) -> str:
    """Return an explicit subject from intake['subject'] or a Subject:/Re: line, else ''."""
    explicit = str(intake.get("subject") or "").strip()
    if explicit:
        return normalize_email_dashes(explicit)
    candidates: list[str] = []
    objective = str(intake.get("objective") or "")
    if objective.strip():
        candidates.extend(objective.splitlines())
    for note in intake.get("verbatim_notes") or []:
        note_str = str(note or "")
        if note_str.strip():
            candidates.extend(note_str.splitlines())
    for line in candidates:
        match = _SUBJECT_OR_RE_LINE.match(line.strip())
        if match and match.group(1).strip():
            return normalize_email_dashes(match.group(1).strip())
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


def _normalize_exhibit_lines(text: str, intake: dict[str, Any]) -> str:
    """Restore canonical Exhibit N: title lines when rhythm pass rewrites colons."""
    canonical: dict[str, tuple[str, str]] = {}
    for note in intake.get("verbatim_notes") or []:
        match = _EXHIBIT_NOTE_RE.match(str(note or "").strip())
        if match:
            ex_num = match.group(1)
            title = match.group(2).strip().rstrip(".")
            canonical[ex_num.lower()] = (ex_num, title)
    if not canonical:
        return text
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        replaced = False
        for ex_num, title in canonical.values():
            pattern = re.compile(
                rf"^\s*{re.escape(ex_num)}\s*"
                rf"(?::|\bis(?:\s+the)?\b|\bcontains\b|\bincludes\b|\bshows\b)\s*"
                rf"(.+?)\.?\s*$",
                re.I,
            )
            match = pattern.match(stripped)
            if match:
                remainder = match.group(1).strip()
                rem_tokens = set(_TOKEN.findall(remainder.lower()))
                allowed_tokens = set(_TOKEN.findall(title.lower())) | {
                    "the",
                    "a",
                    "an",
                }
                if (
                    rem_tokens
                    and rem_tokens <= allowed_tokens
                    and "." not in remainder.rstrip(".")
                ):
                    out_lines.append(f"{ex_num}: {title}")
                    replaced = True
                    break
        if not replaced:
            out_lines.append(line)
    return "\n".join(out_lines)


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
    exhibit_normalized = _normalize_exhibit_lines(rewritten, intake)
    return ensure_email_envelope(
        exhibit_normalized, recipient=recipient, signer=signer, intake=intake
    )


def email_restate_pin_satisfied(draft_text: str, pin_excerpt: str) -> bool:
    """True when draft contains pin text or an email opening rewrite of it."""
    norm_draft = normalize_email_dashes(draft_text)
    draft_tokens = set(_TOKEN.findall(norm_draft.lower())) - _RESTATE_STOPWORDS
    norm_pin = normalize_email_dashes(pin_excerpt)
    for variant in (
        norm_pin,
        rewrite_email_opening(norm_pin),
        normalize_email_dashes(rewrite_email_opening(norm_pin)),
    ):
        anchors = set(_TOKEN.findall(variant.lower())) - _RESTATE_STOPWORDS
        if anchors and anchors <= draft_tokens:
            return True
        if variant.strip().lower() in norm_draft.lower():
            return True
    return False


_CORRESPONDENCE_CHANNELS = frozenset({"email", "sms", "portal", "letter"})
_COLD_HR_DECLINE = re.compile(
    r"\bdeclin(?:e|ing)\b|\bnot\s+available\b|\bunfortunately\b",
    re.I,
)
_OVER_ANSWER_FUTURE = re.compile(
    r"\bother\s+shifts\s+we\s+can\s+schedule\b"
    r"|\bstill\s+happy\s+to\s+stay\s+on\b"
    r"|\bkeep\s+me\s+in\s+mind\s+for\s+future\b"
    r"|\bfuture\s+shifts\b",
    re.I,
)
_AFFIRMATIVE_FUTURE_INVITE = re.compile(
    r"\b(?:offer|mention|include|state|say|ask\s+about|invite|open\s+to|"
    r"available\s+for|happy\s+to\s+stay\s+on|keep\s+me\s+in\s+mind\s+for)\b"
    r"[^.!?\n]{0,60}\b(?:other\s+shifts|future\s+(?:shifts|availability|dates)|"
    r"stay(?:ing)?\s+on|keep\s+me\s+in\s+mind)\b",
    re.I,
)
_FUTURE_INVITE_NEGATION = re.compile(
    r"\b(?:don't|do\s+not|not|no|never|without|avoid|forbid|skip|omit)\b",
    re.I,
)


def _normalize_curly_apostrophes(text: str) -> str:
    return text.replace("\u2019", "'")


def _attested_allows_future_offers(objective: str, verbatim_notes: str) -> bool:
    """True when objective/notes affirmatively invite future-availability language."""
    blob = _normalize_curly_apostrophes(f"{objective}\n{verbatim_notes}")
    for match in _AFFIRMATIVE_FUTURE_INVITE.finditer(blob):
        prefix_start = max(0, match.start() - 30)
        window = blob[prefix_start : match.end()]
        if _FUTURE_INVITE_NEGATION.search(window):
            continue
        return True
    return False


def correspondence_register_penalty(
    text: str,
    *,
    channel: str,
    objective: str = "",
    standing_limits: str = "",
    verbatim_notes: str = "",
) -> int:
    """Penalize cold decline register and unprompted over-answers on correspondence."""
    del standing_limits  # reserved for envelope-specific rules on email
    if channel not in _CORRESPONDENCE_CHANNELS:
        return 0
    penalty = 0
    register_text = _normalize_curly_apostrophes(text)
    if _COLD_HR_DECLINE.search(register_text):
        penalty += 3
    if channel != "email" and ("\u2014" in text or "\u2013" in text):
        penalty += 3
    if _OVER_ANSWER_FUTURE.search(text) and not _attested_allows_future_offers(
        objective, verbatim_notes
    ):
        penalty += 3
    return penalty


def email_draft_penalty(
    text: str,
    *,
    channel: str,
    signer: str,
    standing_limits: str,
    recipient: str = "",
    objective: str = "",
    verbatim_notes: str = "",
) -> int:
    """Score penalty for email envelope/register defects (3 points each)."""
    if channel != "email":
        return 0
    penalty = 0
    if recipient.strip() and not _TO.search(text):
        penalty += 3
    if not _SUBJECT.search(text):
        penalty += 3
    if "reinstatement hearing" in objective.lower():
        subj_match = re.search(r"(?m)^Subject\s*:\s*(.+)$", text, re.I)
        if subj_match and "reinstatement" not in subj_match.group(1).lower():
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
    penalty += correspondence_register_penalty(
        text,
        channel=channel,
        objective=objective,
        standing_limits=standing_limits,
        verbatim_notes=verbatim_notes,
    )
    return penalty
