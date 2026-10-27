"""Deterministic email envelope polish for correspondence channel."""

from __future__ import annotations

import re
from typing import Any

_DIGIT_DASH = re.compile(r"(\d)\s*[\u2013\u2014]\s*(\d)")
_SUBJECT = re.compile(r"(?m)^Subject\s*:", re.I)
_TO = re.compile(r"(?m)^To\s*:", re.I)
_SUBJECT_OR_RE_LINE = re.compile(r"^\s*(?:Subject|Re)\s*:\s*(.+)$", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")
_EXHIBIT_NOTE_RE = re.compile(r"^\s*(Exhibit\s+\d+)\s*:\s*(.+)$", re.I)
_CONTRACTION_EXPANSIONS: tuple[tuple[str, str], ...] = (
    (r"\bwon't\b", "will not"),
    (r"\bcan't\b", "cannot"),
    (r"\bdon't\b", "do not"),
    (r"\bdoesn't\b", "does not"),
    (r"\bdidn't\b", "did not"),
    (r"\bisn't\b", "is not"),
    (r"\baren't\b", "are not"),
    (r"\bwasn't\b", "was not"),
    (r"\bweren't\b", "were not"),
    (r"\bhaven't\b", "have not"),
    (r"\bhasn't\b", "has not"),
    (r"\bhadn't\b", "had not"),
    (r"\bwouldn't\b", "would not"),
    (r"\bcouldn't\b", "could not"),
    (r"\bshouldn't\b", "should not"),
    (r"\bi'm\b", "i am"),
    (r"\bi'll\b", "i will"),
    (r"\bi've\b", "i have"),
    (r"\bi'd\b", "i would"),
)
def normalize_contractions(text: str) -> str:
    """Expand apostrophe contractions so token overlap ignores style variants."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    for pattern, replacement in _CONTRACTION_EXPANSIONS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\bcannot\b", "can not", text, flags=re.I)


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


def _collect_verbatim_spans_for_polish(
    intake: dict[str, Any],
    pin_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Verbatim-attested spans that must survive dash normalization."""
    spans: list[str] = []
    seen: set[str] = set()
    for pin in pin_ledger or []:
        if not isinstance(pin, dict) or pin.get("verbatim_required") is not True:
            continue
        excerpt = _clean_verbatim_pin_excerpt(str(pin.get("excerpt") or ""))
        if excerpt.strip() and excerpt not in seen:
            seen.add(excerpt)
            spans.append(excerpt)
    verbatim_required = intake.get("verbatim_required")
    verbatim_notes = intake.get("verbatim_notes")
    if isinstance(verbatim_required, (list, tuple, set)) and isinstance(
        verbatim_notes, list
    ):
        for idx in verbatim_required:
            if isinstance(idx, int) and 0 <= idx < len(verbatim_notes):
                note = str(verbatim_notes[idx] or "")
                if note.strip() and note not in seen:
                    seen.add(note)
                    spans.append(note)
    spans.sort(key=len, reverse=True)
    return spans


def _clean_verbatim_pin_excerpt(pin_excerpt: str) -> str:
    cleaned = pin_excerpt.replace("&lt;", "<")
    if cleaned.endswith(" [truncated]"):
        cleaned = cleaned[: -len(" [truncated]")]
    return cleaned


def _mask_verbatim_spans(
    text: str, spans: list[str]
) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    masked = text
    for idx, span in enumerate(spans):
        if not span:
            continue
        token = f"__VERBATIM_SPAN_{idx}__"
        replacements[token] = span
        if span in masked:
            masked = masked.replace(span, token, 1)
            continue
        stripped = span.strip()
        if stripped and stripped in masked:
            masked = masked.replace(stripped, token, 1)
    return masked, replacements


def _restore_verbatim_spans(text: str, replacements: dict[str, str]) -> str:
    for token, span in replacements.items():
        text = text.replace(token, span)
    return text


def polish_email_draft(
    text: str,
    *,
    recipient: str,
    signer: str,
    intake: dict[str, Any],
    pin_ledger: list[dict[str, Any]] | None = None,
) -> str:
    """Normalize dashes and enforce email envelope lines."""
    verbatim_spans = _collect_verbatim_spans_for_polish(intake, pin_ledger)
    working, verbatim_map = _mask_verbatim_spans(text, verbatim_spans)
    normalized = normalize_email_dashes(working)
    exhibit_normalized = _normalize_exhibit_lines(normalized, intake)
    restored = _restore_verbatim_spans(exhibit_normalized, verbatim_map)
    return ensure_email_envelope(
        restored, recipient=recipient, signer=signer, intake=intake
    )


def _token_list_lower(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _contiguous_token_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return True
    width = len(needle)
    if width > len(haystack):
        return False
    for start in range(len(haystack) - width + 1):
        if haystack[start : start + width] == needle:
            return True
    return False


def _verbatim_pin_tokens(pin_excerpt: str) -> tuple[str, list[str], bool]:
    was_truncated = pin_excerpt.endswith(" [truncated]")
    cleaned = _clean_verbatim_pin_excerpt(pin_excerpt)
    pin_stripped = cleaned.strip().lower()
    pin_tokens = _token_list_lower(cleaned)
    if was_truncated and len(pin_tokens) > 1:
        pin_tokens = pin_tokens[:-1]
        pin_stripped = re.sub(r"\S+\s*$", "", cleaned.strip()).strip().lower()
    return pin_stripped, pin_tokens, was_truncated


def email_restate_pin_satisfied(draft_text: str, pin_excerpt: str) -> bool:
    """True when the draft satisfies a verbatim-attested pin excerpt."""
    pin_stripped, pin_tokens, _was_truncated = _verbatim_pin_tokens(pin_excerpt)
    draft_lower = draft_text.lower()
    draft_tokens = _token_list_lower(draft_text)
    draft_token_set = set(draft_tokens)
    if not pin_tokens or not all(token in draft_token_set for token in pin_tokens):
        return False
    if pin_stripped in draft_lower:
        return True
    return _contiguous_token_subsequence(pin_tokens, draft_tokens)


def email_draft_penalty(
    text: str,
    *,
    channel: str,
    signer: str,
    recipient: str = "",
) -> int:
    """Mechanical email envelope penalty (3 points each defect)."""
    if channel != "email":
        return 0
    penalty = 0
    if recipient.strip() and not _TO.search(text):
        penalty += 3
    if not _SUBJECT.search(text):
        penalty += 3
    if signer.strip() and not _signer_present(text, signer):
        penalty += 3
    if "\u2014" in text or "\u2013" in text:
        penalty += 3
    return penalty

