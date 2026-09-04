"""Conductor stop vocabulary — parse, validate, resume, score-ratify helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

STOP_TOKENS: frozenset[str] = frozenset(
    {
        "CONSULT_PENDING",
        "CONFIRM_PENDING",
        "ROW_PINNED",
        "HOLD_MERGE",
        "OPERATOR_GATE",
        "PARKED_TRANSPORT",
        "ROW_HOP",
        "DONE",
    }
)

CHAIN_STOPS: frozenset[str] = frozenset({"ROW_HOP"})

WAIT_STOPS: frozenset[str] = frozenset({"CONSULT_PENDING"})
SESSION_STOPS: frozenset[str] = STOP_TOKENS - WAIT_STOPS
EXIT_PERSIST_STOPS: frozenset[str] = frozenset(
    {
        "ROW_PINNED",
        "HOLD_MERGE",
        "OPERATOR_GATE",
        "PARKED_TRANSPORT",
    }
)

_PING_STOPS: frozenset[str] = frozenset(
    {
        "HOLD_MERGE",
        "OPERATOR_GATE",
        "CONFIRM_PENDING",
        "ROW_PINNED",
    }
)

_STOP_CELL_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in STOP_TOKENS) + r")\b"
)
_G_ROW_RE = re.compile(r"^\|\s*(G[1-7])\s*\|", re.MULTILINE)
_FENCE_OPEN_RE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})")
_DESIGNED_STOP_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:>\s+|[-*+]\s+)?"
    r"(?:\*\*stop\*\*:\s*|\*\*stop:\*\*\s*|stop:\s*)("
    + "|".join(re.escape(t) for t in STOP_TOKENS)
    + r")\b"
)
_PACKET_KIND_RE = re.compile(r"(?im)^packet_kind:\s*(\S+)")
_DESIGNED_STOP_DOC_RE = re.compile(
    r"(?im)\bstop:\s*("
    + "|".join(re.escape(t) for t in STOP_TOKENS)
    + r")\b"
)
DESIGNED_STOP_MISSING = "designed_stop_missing"
_RESUME_ROW_RE = re.compile(
    r"(?im)^(?:resume_at|entry_gate|persisted_row):\s*(G[1-7])\b"
)
_MODE_B_ADMIT_RE = re.compile(
    r"(?im)(execution_id:\s*\S+|poll_hint:\s*\S+|status:\s*blocked|honest\s+halt|"
    r'"execution_id"\s*:\s*"[^"]+"|"poll_hint"\s*:\s*"[^"]+")'
)
_RESUME_AT_LINE_RE = re.compile(r"(?im)^(?:resume_at|resumed_at):.*$")
_RESUME_AT_JSON_RE = re.compile(
    r'"(?:resume_at|resumed_at)"\s*:\s*"[^"]*"'
)
_ARCHIVE_OR_HARVEST_RE = re.compile(
    r"(?im)(archive_uri:\s*\S+|from:\s*web-anthropic|from_agent:\s*web-anthropic)"
)
_NEXT_ADMIT_RE = re.compile(r"(?im)\bNEXT_ADMIT\b")
_NEXT_ADMIT_NONE_RE = re.compile(r"(?im)^NEXT_ADMIT\s*:\s*none\b")
_NEXT_ADMIT_HARVEST_RE = re.compile(r"(?im)^NEXT_ADMIT\s*:\s*(?!none\b)\S+")
_SCORE_RATIFY_MARKERS = (
    "do-not-fight",
    "do not fight",
    "likely-optimal",
    "likely optimal",
    "likely_optimal",
)
_STOP_AFTER_G1_RE = re.compile(r"(?im)stop_after(?:\s+pin)?\s*:\s*G1\b")
_PROBLEM_RE = re.compile(r"(?im)^(?:\*\*)?problem(?:\*\*)?\s*:\s*\S")
_SCOPE_RE = re.compile(r"(?im)^(?:\*\*)?scope(?:\*\*)?\s*:\s*\S")
_ACCEPTANCE_RE = re.compile(r"(?im)^(?:\*\*)?acceptance(?:\*\*)?\s*:\s*\S")
_DENSITY_TRIAGE_RE = re.compile(
    r"(?im)^(?:\*\*)?density_triage(?:\*\*)?\s*:\s*(\S+)"
)
S4B_G1_PIN_MISSING = "s4b_g1_pin_missing"


@dataclass(frozen=True, slots=True)
class StopParseResult:
    """Parsed stop tokens from a scoreboard row or closeout body."""

    tokens: frozenset[str]
    rows: dict[str, frozenset[str]]
    malformed: tuple[str, ...]
    designed_tokens: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CloseoutStopVerdict:
    """Result of validating a conductor closeout against stop catalog."""

    ok: bool
    reason: str | None = None
    resume_row: str | None = None
    pings_required: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ScoreRatifyVerdict:
    """Whether an in-process score-ratify packet meets Q2 posture."""

    ok: bool
    reason: str | None = None


def _strip_narrative_resume_at(text: str) -> str:
    """Remove resume_at/resumed_at lines and JSON values before stop-token scan."""
    stripped = _RESUME_AT_LINE_RE.sub("", text or "")
    return _RESUME_AT_JSON_RE.sub("", stripped)


def _fenced_spans(body: str) -> tuple[tuple[int, int], ...]:
    """Return source offsets enclosed by Markdown backtick or tilde fences."""
    spans: list[tuple[int, int]] = []
    fence_start: int | None = None
    marker_char = ""
    marker_width = 0
    offset = 0
    for line in body.splitlines(keepends=True):
        match = _FENCE_OPEN_RE.match(line)
        if match is not None:
            marker = match.group("marker")
            if fence_start is None:
                fence_start = offset
                marker_char = marker[0]
                marker_width = len(marker)
            elif marker[0] == marker_char and len(marker) >= marker_width:
                spans.append((fence_start, offset + len(line)))
                fence_start = None
                marker_char = ""
                marker_width = 0
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(body)))
    return tuple(spans)


def _in_fenced_span(spans: tuple[tuple[int, int], ...], offset: int) -> bool:
    """True when *offset* starts inside a fenced region."""
    return any(start <= offset < end for start, end in spans)


def _is_conductor_packet(
    packet_text: str | None,
    *,
    packet_kind: str | None = None,
) -> bool:
    """True when packet is a conductor mission."""
    if packet_kind == "conductor":
        return True
    if packet_text:
        match = _PACKET_KIND_RE.search(packet_text)
        if match and match.group(1).strip().lower() == "conductor":
            return True
    return False


def _iter_designed_stop_matches(text: str):
    """Yield designed-stop regex matches outside fenced code blocks."""
    body = text or ""
    fenced = _fenced_spans(body)
    for match in _DESIGNED_STOP_LINE_RE.finditer(body):
        if not _in_fenced_span(fenced, match.start()):
            yield match


def parse_stop_tokens(text: str) -> StopParseResult:
    """Extract stop tokens from scoreboard markdown or closeout prose."""
    scan_text = _strip_narrative_resume_at(text)
    tokens: set[str] = set()
    malformed: list[str] = []
    rows: dict[str, set[str]] = {}
    for match in _STOP_CELL_RE.finditer(scan_text):
        token = match.group(1).upper()
        if token in STOP_TOKENS:
            tokens.add(token)
        else:
            malformed.append(token)
    for row_match in _G_ROW_RE.finditer(text or ""):
        gid = row_match.group(1)
        row_start = row_match.start()
        next_row = _G_ROW_RE.search(text, row_match.end())
        row_end = next_row.start() if next_row else len(text)
        row_text = _strip_narrative_resume_at(text[row_start:row_end])
        row_tokens = frozenset(
            m.group(1).upper() for m in _STOP_CELL_RE.finditer(row_text)
        )
        if row_tokens:
            rows[gid] = row_tokens
    return StopParseResult(
        tokens=frozenset(tokens),
        rows={k: frozenset(v) for k, v in rows.items()},
        malformed=tuple(malformed),
    )


def parse_designed_stop_tokens(text: str) -> StopParseResult:
    """Extract mission-close tokens only from explicit ``stop:`` footer lines.

    G-row progress prose (e.g. ``| G1 | … | DONE |``) is excluded from
    ``tokens`` / ``designed_tokens`` so ``ROW_HOP`` closeouts do not bleed
    table-cell ``DONE`` into mission-close authority. Row-level stops still
    come from the full table scan via ``rows``.
    """
    designed: set[str] = set()
    malformed: list[str] = []
    for match in _iter_designed_stop_matches(text or ""):
        token = match.group(1).upper()
        if token in STOP_TOKENS:
            designed.add(token)
        else:
            malformed.append(token)
    full = parse_stop_tokens(text)
    designed_frozen = frozenset(designed)
    return StopParseResult(
        tokens=designed_frozen,
        rows=full.rows,
        malformed=tuple(malformed),
        designed_tokens=designed_frozen,
    )


def validate_stop_token(token: str) -> bool:
    """True when token is in the sealed stop catalog."""
    return token.strip().upper() in STOP_TOKENS


def validate_conductor_packet(
    packet_text: str,
    *,
    packet_kind: str | None = None,
) -> CloseoutStopVerdict:
    """Require conductor spawn packets to document at least one designed stop."""
    if not _is_conductor_packet(packet_text, packet_kind=packet_kind):
        return CloseoutStopVerdict(ok=True)
    if _DESIGNED_STOP_DOC_RE.search(packet_text or ""):
        return CloseoutStopVerdict(ok=True)
    return CloseoutStopVerdict(
        ok=False,
        reason=DESIGNED_STOP_MISSING,
    )


def is_exit_persist_stop(body: str) -> bool:
    """True when closeout carries an exit-and-persist token (Mission E §5)."""
    parsed = parse_stop_tokens(body)
    return bool(parsed.tokens & EXIT_PERSIST_STOPS)


def is_consult_pending_wait(body: str) -> bool:
    """True when CONSULT_PENDING is a wait token with live admit and no harvest.

    Chrome-only presence is not harvest — ``archive_uri`` or ``from=web-anthropic``
    clears the wait. Used by GIW to suppress ``gate_d`` / ``work`` grading.
    """
    parsed = parse_stop_tokens(body)
    if "CONSULT_PENDING" not in parsed.tokens:
        return False
    text = body or ""
    if not _MODE_B_ADMIT_RE.search(text):
        return False
    if _ARCHIVE_OR_HARVEST_RE.search(text):
        return False
    return True


def has_consult_handoff(body: str) -> bool:
    """True when a CONSULT_PENDING wrapper named the next admit (NEXT_ADMIT)."""
    return _NEXT_ADMIT_RE.search(body or "") is not None


def next_admit_names_harvest(body: str) -> bool:
    """True on NEXT_ADMIT naming a non-none harvest target; False on none or absent."""
    text = body or ""
    if _NEXT_ADMIT_NONE_RE.search(text):
        return False
    return _NEXT_ADMIT_HARVEST_RE.search(text) is not None


def resume_row_from_closeout(body: str, *, default: str = "G1") -> str:
    """Return persisted G-row for resume-after-terminal re-admit."""
    match = _RESUME_ROW_RE.search(body or "")
    if match:
        return match.group(1).upper()
    parsed = parse_stop_tokens(body)
    for gid in sorted(parsed.rows.keys()):
        if "ROW_PINNED" in parsed.rows[gid]:
            return gid
    return default.upper()


def pings_for_stops(
    tokens: frozenset[str],
    *,
    live_summoning_chat: bool = False,
    operator_present: bool = False,
) -> frozenset[str]:
    """Return stop tokens that require operator ping per ping table.

    ``live_summoning_chat`` alone does not suppress ``ROW_PINNED`` — liaison
    IDE is not operator-present (9638#187). Only ``operator_present`` drops it.
    """
    pings = tokens & _PING_STOPS
    if live_summoning_chat and operator_present:
        return pings - frozenset({"ROW_PINNED"})
    return pings


def is_g1_pin(body: str, *, packet_text: str | None = None) -> bool:
    """True when this closeout is a G1-pin, not a later-row see-score pin."""
    text = body or ""
    parsed = parse_stop_tokens(text)
    for gid in ("G6", "G5", "G4", "G3", "G2"):
        if "ROW_PINNED" in parsed.rows.get(gid, frozenset()):
            return False
    resume_match = _RESUME_ROW_RE.search(text)
    if resume_match and resume_match.group(1).upper() != "G1":
        return False
    if "ROW_PINNED" in parsed.rows.get("G1", frozenset()):
        return True
    if _STOP_AFTER_G1_RE.search(text) and "ROW_PINNED" in parsed.tokens:
        return True
    if (
        packet_text
        and _STOP_AFTER_G1_RE.search(packet_text)
        and "ROW_PINNED" in parsed.tokens
    ):
        return True
    return False


def has_s4b_evidence(body: str) -> bool:
    """True when closeout carries S4b rich-seed densify markers (bind i / S4b)."""
    text = body or ""
    if not (
        _PROBLEM_RE.search(text)
        and _SCOPE_RE.search(text)
        and _ACCEPTANCE_RE.search(text)
    ):
        return False
    triage_match = _DENSITY_TRIAGE_RE.search(text)
    if triage_match is None:
        return False
    return triage_match.group(1).strip().lower() != "implement_ready"


def validate_s4b_g1_pin(body: str, *, packet_text: str | None = None) -> str | None:
    """Return ``s4b_g1_pin_missing`` when G1-pin lacks S4b evidence; else None."""
    if not is_g1_pin(body, packet_text=packet_text):
        return None
    if has_s4b_evidence(body):
        return None
    return S4B_G1_PIN_MISSING


def validate_conductor_closeout(
    body: str,
    *,
    require_mode_b_proof: bool = False,
    live_summoning_chat: bool = False,
    operator_present: bool = False,
    packet_text: str | None = None,
    packet_kind: str | None = None,
) -> CloseoutStopVerdict:
    """Validate closeout stop vocabulary + optional Mode B admit-proof."""
    if _is_conductor_packet(packet_text, packet_kind=packet_kind):
        designed = parse_designed_stop_tokens(body)
        if not designed.designed_tokens:
            return CloseoutStopVerdict(
                ok=False,
                reason=DESIGNED_STOP_MISSING,
            )
    parsed = parse_stop_tokens(body)
    if parsed.malformed:
        return CloseoutStopVerdict(
            ok=False,
            reason=f"malformed stop token(s): {', '.join(parsed.malformed)}",
        )
    unknown = re.findall(
        r"\b([A-Z]{3,}(?:_[A-Z]+)+)\b",
        body or "",
    )
    for candidate in unknown:
        if candidate.endswith("_PENDING") and candidate not in STOP_TOKENS:
            if candidate != "IMPLEMENT_READY":
                return CloseoutStopVerdict(
                    ok=False,
                    reason=f"unknown stop token: {candidate}",
                )
    if require_mode_b_proof and "CONSULT_PENDING" in parsed.tokens:
        if not _MODE_B_ADMIT_RE.search(body or ""):
            return CloseoutStopVerdict(
                ok=False,
                reason="Mode B admit-proof missing: execution_id+poll_hint or honest halt",
            )
    s4b_reason = validate_s4b_g1_pin(body, packet_text=packet_text)
    if s4b_reason is not None:
        return CloseoutStopVerdict(ok=False, reason=s4b_reason)
    from claude_bundles.conductor_score_ratify import validate_q2_away_score_ratify

    q2_reason = validate_q2_away_score_ratify(
        body,
        packet_text=packet_text,
    )
    if q2_reason is not None:
        return CloseoutStopVerdict(ok=False, reason=q2_reason)
    resume = resume_row_from_closeout(body)
    return CloseoutStopVerdict(
        ok=True,
        resume_row=resume,
        pings_required=pings_for_stops(
            parsed.tokens,
            live_summoning_chat=live_summoning_chat,
            operator_present=operator_present,
        ),
    )


def validate_score_ratify_packet(body: str) -> ScoreRatifyVerdict:
    """Check G3→G5 in-process score-ratify carries do-not-fight / likely-optimal."""
    lower = (body or "").lower()
    if any(marker in lower for marker in _SCORE_RATIFY_MARKERS):
        return ScoreRatifyVerdict(ok=True)
    return ScoreRatifyVerdict(
        ok=False,
        reason="score-ratify packet missing do-not-fight / likely-optimal posture",
    )


def score_ratify_packet_template(*, scoreboard_uri: str, todo_ref: str) -> str:
    """Minimal in-process CDP review packet for default G3→G5 score-ratify."""
    return "\n".join(
        [
            f"# Score-ratify — {todo_ref}",
            "",
            f"Scoreboard: {scoreboard_uri}",
            "",
            "Posture: do-not-fight the mission; judge likely-optimal completion only.",
            "This is score-ratify, not CONFIRM_PENDING (confirmation kernel).",
            "purpose=review · default web-anthropic / CDP.",
        ]
    )
