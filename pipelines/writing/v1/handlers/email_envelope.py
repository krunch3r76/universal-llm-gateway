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
        "am",
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
_DECLINE_ABILITY_NOT_ABLE = re.compile(
    r"\b(?:(?:am|is|are)\s+not|will\s+not(?:\s+be)?|would\s+not\s+be|"
    r"(?:am|are|is)\s+not\s+going\s+to\s+be)\s+able\s+to\b",
    re.I,
)
_CAN_NOT_MODAL = re.compile(r"\bcan not\b", re.I)
_DO_NOT_THINK = re.compile(r"\b(?:do|does|did)\s+not\s+think\b", re.I)
_PROVIDE_WORD = re.compile(r"\bprovide\b", re.I)
_DO_NOT_THINK_ABILITY_REFUSAL = re.compile(
    r"^(?:do|does|did)\s+not\s+think\s+(?:that\s+)?"
    r"(?:i|we)\s+(?:can|could|will|would|am|are|is)\s+"
    r"(?!(?:not|never)\s)(?!(?:refuse|unable)\b)"
    r"(?:be\s+able\s+to\s+)?provide\b",
    re.I,
)
_POLARITY_SUBCLAUSE_BOUNDARY = re.compile(r"\b(?:so|because|and)\b", re.I)
_PROVIDE_REFUSAL_SCOPE_BOUNDARY = re.compile(
    r"(?:[,;]|\b(?:but|so|because|and|though|although|while)\b)",
    re.I,
)
_MODAL_NOT_PROVIDE_SINGLE_ADVERBS = frozenset(
    {
        "actually",
        "always",
        "currently",
        "easily",
        "ever",
        "feasibly",
        "honestly",
        "just",
        "now",
        "possibly",
        "presently",
        "readily",
        "really",
        "reliably",
        "simply",
        "today",
        "yet",
    }
)
_COORDINATED_INFINITIVE_BEFORE_PROVIDE = re.compile(
    r"^(?:commit|agree|promise|undertake|manage|afford)\s+to\s+(?:or|and)$",
    re.I,
)
_NEGATED_PROVIDE_GOVERNANCE = re.compile(
    r"\b(?:no|not|never|unable|decline|declining|without)\b(?:\s+\w+){0,3}\s+\bprovide\b",
    re.I,
)
_NEGATED_PROVIDE_GOVERNANCE_STRICT = re.compile(
    r"\b(?:no|not|never|unable|without)\b"
    r"(?:\s+(?!only\b|wait\b|see\b|think\b)\w+){0,2}\s+\bprovide\b",
    re.I,
)
_UNABLE_TO = re.compile(r"\bunable\s+to\b", re.I)
_SUPPLY_VERBS = re.compile(
    r"\b(?:provide|send|share|give|submit)\b",
    re.I,
)
_RESTATE_NEGATION_TOKENS = frozenset(
    {
        "decline",
        "declining",
        "never",
        "no",
        "not",
        "unable",
        "without",
    }
)
_RESTATE_OPTIONAL_TOKENS = frozenset({"currently"})
_NEGATION_GUARD_AUXILIARY = frozenset(
    {
        "able",
        "can",
        "could",
        "did",
        "do",
        "does",
        "had",
        "has",
        "have",
        "should",
        "will",
        "would",
    }
)


def normalize_contractions(text: str) -> str:
    """Expand apostrophe contractions so token overlap ignores style variants."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    for pattern, replacement in _CONTRACTION_EXPANSIONS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\bcannot\b", "can not", text, flags=re.I)


def normalize_decline_ability_phrases(text: str) -> str:
    """Map inability phrasing to the same tokens as ``cannot`` / ``can't``."""
    text = _DECLINE_ABILITY_NOT_ABLE.sub("can not", text)
    return _UNABLE_TO.sub("can not", text)


def normalize_temporal_monthly_synonyms(text: str) -> str:
    """Align restate checks for ``right now`` vs ``currently`` without conflating monthly."""
    return re.sub(r"\bright\s+now\b", "currently", text, flags=re.I)


_CLAUSE_BOUNDARY = re.compile(
    r"(?:[,;:\n]|\s*[\u2014\u2013-]\s*|\b(?:but|yet|however|though)\b)\s*",
    re.I,
)


def _clean_verbatim_pin_excerpt(pin_excerpt: str) -> str:
    """Unfence assemble truncation markers for verbatim pin comparison."""
    cleaned = pin_excerpt.replace("&lt;", "<")
    if cleaned.endswith(" [truncated]"):
        cleaned = cleaned[: -len(" [truncated]")]
    return cleaned


def _verbatim_pin_tokens(pin_excerpt: str) -> tuple[str, list[str], bool]:
    """Return lower stripped pin text, token list, and whether assemble truncated mid-word."""
    was_truncated = pin_excerpt.endswith(" [truncated]")
    cleaned = _clean_verbatim_pin_excerpt(pin_excerpt)
    pin_stripped = cleaned.strip().lower()
    pin_tokens = _token_list_lower(cleaned)
    if was_truncated and len(pin_tokens) > 1:
        pin_tokens = pin_tokens[:-1]
        pin_stripped = re.sub(r"\S+\s*$", "", cleaned.strip()).strip().lower()
    return pin_stripped, pin_tokens, was_truncated


def normalize_supply_verbs(text: str) -> str:
    """Map delivery/supply verbs to ``provide`` for non-verbatim restate overlap.

    Equivalence ``provide`` ↔ ``send`` / ``share`` / ``give`` / ``submit`` applies
    in ``normalize_text_for_token_match`` for restate token sets and
    ``provenance_check`` anchor overlap. ``_restate_negation_guard`` still requires
    refusal polarity on every draft segment whose tokens cover those content anchors.
    """
    return _SUPPLY_VERBS.sub("provide", text)


def normalize_text_for_token_match(text: str) -> str:
    """Normalize dashes, contractions, and decline phrasing before token extraction."""
    text = normalize_email_dashes(text)
    text = normalize_contractions(text)
    text = normalize_decline_ability_phrases(text)
    text = normalize_supply_verbs(text)
    return normalize_temporal_monthly_synonyms(text)


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


def _collect_verbatim_spans_for_polish(
    intake: dict[str, Any],
    pin_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Verbatim-attested spans that must survive opening rewrite and dash normalization."""
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


def _mask_verbatim_spans(
    text: str, spans: list[str]
) -> tuple[str, dict[str, str]]:
    """Replace verbatim spans with placeholders before destructive polish passes."""
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
    """Rewrite opening, normalize dashes, and enforce email envelope lines."""
    verbatim_spans = _collect_verbatim_spans_for_polish(intake, pin_ledger)
    working, verbatim_map = _mask_verbatim_spans(text, verbatim_spans)
    normalized = normalize_email_dashes(working)
    rewritten = rewrite_email_opening(normalized)
    exhibit_normalized = _normalize_exhibit_lines(rewritten, intake)
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


def _restate_anchor_tokens(normalized_pin_text: str) -> set[str]:
    anchors = (
        set(_TOKEN.findall(normalized_pin_text.lower())) - _RESTATE_STOPWORDS
    )
    optional = anchors & _RESTATE_OPTIONAL_TOKENS
    if optional and len(anchors - _RESTATE_OPTIONAL_TOKENS) >= 3:
        anchors -= _RESTATE_OPTIONAL_TOKENS
    return anchors


def _draft_clauses(text: str) -> list[str]:
    clauses: list[str] = []
    for sentence in re.split(r"[.!?]+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        for piece in _CLAUSE_BOUNDARY.split(sentence):
            piece = piece.strip()
            if piece:
                clauses.append(piece)
    return clauses if clauses else [text]


def _polarity_subclauses(text: str) -> list[str]:
    """Split draft text for affirmative-pin polarity checks, including so/because/and."""
    clauses: list[str] = []
    for clause in _draft_clauses(text):
        for piece in _POLARITY_SUBCLAUSE_BOUNDARY.split(clause):
            piece = piece.strip()
            if piece:
                clauses.append(piece)
    return clauses if clauses else [text]


def _negation_tokens(token_set: set[str]) -> set[str]:
    return token_set & _RESTATE_NEGATION_TOKENS


def _restate_content_anchors(anchors: set[str]) -> set[str]:
    """Content-bearing pin tokens used to locate the clause that must keep refusal polarity."""
    return anchors - _RESTATE_NEGATION_TOKENS - _NEGATION_GUARD_AUXILIARY


def _restate_clause_tokens(clause: str) -> set[str]:
    norm_clause = normalize_text_for_token_match(clause)
    return set(_TOKEN.findall(norm_clause.lower())) - _RESTATE_STOPWORDS


def _restate_text_segments(text: str) -> list[str]:
    """Sentences plus comma clauses, deduped, for localized restate polarity checks."""
    segments: list[str] = []
    seen: set[str] = set()
    for sentence in re.split(r"[.!?]+", text):
        sentence = sentence.strip()
        if sentence and sentence not in seen:
            seen.add(sentence)
            segments.append(sentence)
    for clause in _draft_clauses(text):
        if clause not in seen:
            seen.add(clause)
            segments.append(clause)
    return segments if segments else [text]


def _pin_segment_has_provide_verb(pin_segment: str) -> bool:
    norm = normalize_text_for_token_match(pin_segment).lower()
    return "provide" in _TOKEN.findall(norm)


def _provide_refusal_windows(norm_seg: str) -> list[tuple[int, int, str]]:
    """Collect negator-through-``provide`` spans as ``(start, end, text)`` for validation."""
    windows: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()

    def _add(start: int, end: int, window: str) -> None:
        window = window.strip()
        key = (start, end)
        if window and key not in seen:
            seen.add(key)
            windows.append((start, end, window))

    for cm in _CAN_NOT_MODAL.finditer(norm_seg):
        rest = norm_seg[cm.start() :]
        pm = _PROVIDE_WORD.search(rest)
        if pm:
            end = cm.start() + pm.end()
            _add(cm.start(), end, rest[: pm.end()])

    for tm in _DO_NOT_THINK.finditer(norm_seg):
        rest = norm_seg[tm.start() :]
        pm = _PROVIDE_WORD.search(rest)
        if pm:
            end = tm.start() + pm.end()
            _add(tm.start(), end, rest[: pm.end()])

    for matcher in (_NEGATED_PROVIDE_GOVERNANCE_STRICT, _NEGATED_PROVIDE_GOVERNANCE):
        for match in matcher.finditer(norm_seg):
            start = match.start()
            rest = norm_seg[start:]
            pm = _PROVIDE_WORD.search(rest)
            if pm:
                end = start + pm.end()
                _add(start, end, rest[: pm.end()])

    windows.sort(key=lambda item: (item[0], item[1]))
    return windows


def _provide_refusal_outer_vetoes_inner(
    norm_seg: str,
    outer: tuple[int, int, str],
    inner: tuple[int, int, str],
) -> bool:
    """True when a rejected outer negator governs the same clause as a nested inner window."""
    start_outer, end_outer, w_outer = outer
    start_inner, end_inner, _w_inner = inner
    if end_inner != end_outer or start_inner <= start_outer:
        return False
    if _PROVIDE_REFUSAL_SCOPE_BOUNDARY.search(norm_seg[start_outer:start_inner]):
        return False
    return True


def _provide_refusal_vetoed_windows(
    norm_seg: str,
    windows: list[tuple[int, int, str]],
    genuine: dict[tuple[int, int], bool],
) -> set[tuple[int, int]]:
    """Inner windows vetoed when an outer rejected negator shares the same ``provide`` span."""
    vetoed: set[tuple[int, int]] = set()
    for outer in windows:
        outer_key = (outer[0], outer[1])
        if genuine.get(outer_key, False):
            continue
        for inner in windows:
            inner_key = (inner[0], inner[1])
            if inner_key == outer_key:
                continue
            if _provide_refusal_outer_vetoes_inner(norm_seg, outer, inner):
                vetoed.add(inner_key)
    return vetoed


def _provide_refusal_see_path_genuine(window: str) -> bool:
    """True for ``can not see how/a way … provide`` when the bridge is not another negation."""
    w = window.strip()
    if not re.match(r"^(?:can not|could not)\s+see\b", w):
        return False
    if re.search(r"\bwhy\s+(?:i|we)\s+would\s+not\b", w):
        return False
    path = re.search(r"\b(?:a way|how|any way)\b", w)
    if not path:
        return False
    tail = w[path.end() :]
    pm = _PROVIDE_WORD.search(tail)
    if not pm:
        return True
    bridge = tail[: pm.start()]
    if "," in bridge or ";" in bridge:
        return False
    if re.search(r"\b(?:not|never|unable|refuse|decline)\b", bridge):
        return False
    return True


def _modal_not_provide_adverb_allowed(tok: str) -> bool:
    """True when one filler token is an allowed adverb before ``provide`` in a modal refusal."""
    lower = tok.lower()
    if lower in _MODAL_NOT_PROVIDE_SINGLE_ADVERBS:
        return True
    return lower.endswith("ly") and lower not in {"only"}


def _modal_not_through_provide_genuine(window: str) -> bool:
    """True for ``can not`` … ``provide`` when middle filler matches the closed allowlist."""
    w = window.strip().lower()
    if w == "can not provide":
        return True
    m = re.match(r"^can not\s+(.+)$", w)
    if not m:
        return False
    tail = m.group(1)
    pm = _PROVIDE_WORD.search(tail)
    if not pm or pm.end() != len(tail.rstrip()):
        return False
    middle = tail[: pm.start()].strip()
    if "," in middle or ";" in middle:
        return False
    if not middle:
        return True
    fillers = _TOKEN.findall(middle)
    if len(fillers) == 1:
        return _modal_not_provide_adverb_allowed(fillers[0])
    if _COORDINATED_INFINITIVE_BEFORE_PROVIDE.match(middle):
        return True
    if len(fillers) == 4:
        adverb, verb, to_tok, conj = fillers
        if to_tok != "to" or conj not in {"or", "and"}:
            return False
        coord = " ".join(fillers[1:])
        return _modal_not_provide_adverb_allowed(
            adverb
        ) and bool(_COORDINATED_INFINITIVE_BEFORE_PROVIDE.match(coord))
    return False


def _provide_refusal_window_is_genuine(window: str) -> bool:
    """True when one negator-through-``provide`` span is a real supply refusal."""
    w = window.strip()
    if not w:
        return False
    if re.match(r"^can not wait to\b", w):
        return False
    if re.match(r"^can not only\b", w):
        return False
    if re.match(r"^can not help but\b", w) or re.search(r"\bhelp but\b", w):
        return False
    if re.search(r"\bwhy\s+(?:i|we)\s+would\s+not\b", w):
        return False
    if re.match(r"^(?:do|does|did)\s+not\s+think\b", w):
        if re.search(r"\bso\s+(?:i|we)\s+will\s+provide\b", w):
            return False
        return bool(_DO_NOT_THINK_ABILITY_REFUSAL.match(w))
    if re.match(r"^(?:can not|could not)\s+see\b", w):
        return _provide_refusal_see_path_genuine(w)
    if re.match(r"^(?:will|would)\s+not\s+provide\b", w):
        return False
    if re.match(r"^can not\b", w):
        return _modal_not_through_provide_genuine(w)
    if re.match(r"^not provide\b", w):
        return False
    if re.match(r"^(?:never|no|unable|without)\b", w):
        return True
    return False


def _draft_segment_satisfies_provide_refusal(draft_segment: str) -> bool:
    """True when normalized draft segment contains a genuine supply refusal of ``provide``.

    Outer negator windows that fail genuineness veto nested inner windows in the
    same ``provide`` span (fail-closed over same-clause inner matches).
    """
    norm_seg = normalize_text_for_token_match(draft_segment).lower()
    if re.search(r"\b(?:decline|declining)\b", norm_seg):
        return True
    windows = _provide_refusal_windows(norm_seg)
    genuine = {
        (start, end): _provide_refusal_window_is_genuine(window)
        for start, end, window in windows
    }
    vetoed = _provide_refusal_vetoed_windows(norm_seg, windows, genuine)
    return any(
        ok and (start, end) not in vetoed for (start, end), ok in genuine.items()
    )


def _restate_negation_guard(draft_text: str, pin_excerpt: str) -> bool:
    """Require negation on every draft span that carries a refusing pin's content."""
    draft_segments = _restate_text_segments(draft_text)
    polarity_clauses = _polarity_subclauses(draft_text)
    for pin_segment in _restate_text_segments(pin_excerpt):
        pin_segment_tokens = _restate_clause_tokens(pin_segment)
        content_anchors = _restate_content_anchors(
            _restate_anchor_tokens(normalize_text_for_token_match(pin_segment))
        )
        if not content_anchors:
            continue
        if _negation_tokens(pin_segment_tokens):
            matched_any = False
            for draft_segment in draft_segments:
                draft_segment_tokens = _restate_clause_tokens(draft_segment)
                if not content_anchors <= draft_segment_tokens:
                    continue
                matched_any = True
                if not _negation_tokens(draft_segment_tokens):
                    return False
                if _pin_segment_has_provide_verb(pin_segment):
                    if not _draft_segment_satisfies_provide_refusal(draft_segment):
                        return False
            if not matched_any:
                return False
        else:
            for clause in polarity_clauses:
                clause_tokens = _restate_clause_tokens(clause)
                if not content_anchors <= clause_tokens:
                    continue
                if _negation_tokens(clause_tokens):
                    return False
    return True


def email_restate_pin_satisfied(
    draft_text: str,
    pin_excerpt: str,
    *,
    verbatim_required: bool = False,
) -> bool:
    """True when the draft satisfies a restate or verbatim-attested pin excerpt."""
    if verbatim_required:
        pin_stripped, pin_tokens, _was_truncated = _verbatim_pin_tokens(pin_excerpt)
        draft_lower = draft_text.lower()
        draft_tokens = _token_list_lower(draft_text)
        draft_token_set = set(draft_tokens)
        if not pin_tokens or not all(token in draft_token_set for token in pin_tokens):
            return False
        if pin_stripped in draft_lower:
            return True
        return _contiguous_token_subsequence(pin_tokens, draft_tokens)

    norm_draft = normalize_text_for_token_match(draft_text)
    draft_tokens = set(_TOKEN.findall(norm_draft.lower())) - _RESTATE_STOPWORDS
    norm_pin = normalize_email_dashes(pin_excerpt)
    for variant in (
        norm_pin,
        rewrite_email_opening(norm_pin),
        normalize_email_dashes(rewrite_email_opening(norm_pin)),
    ):
        norm_variant = normalize_text_for_token_match(variant)
        anchors = _restate_anchor_tokens(norm_variant)
        if anchors and anchors <= draft_tokens:
            if _restate_negation_guard(draft_text, pin_excerpt):
                return True
            continue
        if variant.strip().lower() in normalize_email_dashes(draft_text).lower():
            if _restate_negation_guard(draft_text, pin_excerpt):
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


_AFFIRMATIVE_LIST_FORMAT = re.compile(
    r"\b(?:numbered\s+list|bullet(?:ed)?\s+list|bullet\s+points|"
    r"in\s+outline\s+format|as\s+a\s+(?:numbered\s+)?list|"
    r"use\s+bullet(?:s|\s+points))\b",
    re.I,
)
_LIST_FORMAT_NEGATION = re.compile(
    r"\b(?:don't|do\s+not|not|no|never|without|avoid)\b",
    re.I,
)
_NUMBERED_OR_BULLET_LINE = re.compile(r"(?m)^\s*(?:\d+\.\s+|[-*•]\s+)")
_EXHIBIT_LINE = re.compile(r"^\s*Exhibit\s+\d+\s*:", re.I)
_THROAT_CLEARING = re.compile(
    r"\bplease\s+note\s+the\s+following\b|\bi\s+am\s+responding\s+to\s+your\s+request\b",
    re.I,
)
def _list_format_allowed(objective: str, verbatim_notes: str) -> bool:
    """True when objective/notes affirmatively request a list or outline format."""
    blob = _normalize_curly_apostrophes(f"{objective}\n{verbatim_notes}")
    for match in _AFFIRMATIVE_LIST_FORMAT.finditer(blob):
        prefix_start = max(0, match.start() - 30)
        window = blob[prefix_start : match.start()]
        if _LIST_FORMAT_NEGATION.search(window):
            continue
        return True
    return False


def _sms_formal_signoff(text: str, signer: str) -> bool:
    """True when SMS body ends with a formal thank-you line plus the signer name."""
    signer = signer.strip()
    if not signer:
        return False
    tokens = signer.split()
    first = tokens[0] if tokens else signer
    if first != signer:
        name_part = f"(?:{re.escape(signer)}|{re.escape(first)})"
    else:
        name_part = re.escape(signer)
    return bool(
        re.search(
            rf"(?:thank\s+you|thanks|sincerely|regards|best)[,!]?\s+"
            rf"{name_part}\s*\.?\s*$",
            text.strip(),
            re.I,
        )
    )


def _correspondence_has_list_body(text: str) -> bool:
    for line in text.splitlines():
        if _EXHIBIT_LINE.match(line):
            continue
        if _NUMBERED_OR_BULLET_LINE.match(line):
            return True
    return False


def correspondence_register_penalty(
    text: str,
    *,
    channel: str,
    objective: str = "",
    standing_limits: str = "",
    verbatim_notes: str = "",
    signer: str = "",
    signer_verbatim_required: bool = False,
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
    if channel in {"email", "sms"}:
        if (
            channel == "sms"
            and signer.strip()
            and not signer_verbatim_required
            and _sms_formal_signoff(text, signer)
        ):
            penalty += 3
        if not _list_format_allowed(objective, verbatim_notes):
            if _correspondence_has_list_body(text):
                penalty += 3
        if _THROAT_CLEARING.search(register_text):
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
        signer=signer,
    )
    return penalty
