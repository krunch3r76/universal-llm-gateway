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
        "DONE",
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
_G_ROW_RE = re.compile(r"^\|\s*(G[1-6])\s*\|", re.MULTILINE)
_RESUME_ROW_RE = re.compile(
    r"(?im)^(?:resume_at|entry_gate|persisted_row):\s*(G[1-6])\b"
)
_MODE_B_ADMIT_RE = re.compile(
    r"(?im)(execution_id:\s*\S+|poll_hint:\s*\S+|status:\s*blocked|honest\s+halt)"
)
_SCORE_RATIFY_MARKERS = (
    "do-not-fight",
    "do not fight",
    "likely-optimal",
    "likely optimal",
    "likely_optimal",
)


@dataclass(frozen=True, slots=True)
class StopParseResult:
    """Parsed stop tokens from a scoreboard row or closeout body."""

    tokens: frozenset[str]
    rows: dict[str, frozenset[str]]
    malformed: tuple[str, ...]


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


def parse_stop_tokens(text: str) -> StopParseResult:
    """Extract stop tokens from scoreboard markdown or closeout prose."""
    tokens: set[str] = set()
    malformed: list[str] = []
    rows: dict[str, set[str]] = {}
    for match in _STOP_CELL_RE.finditer(text or ""):
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
        row_text = text[row_start:row_end]
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


def validate_stop_token(token: str) -> bool:
    """True when token is in the sealed stop catalog."""
    return token.strip().upper() in STOP_TOKENS


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


def pings_for_stops(tokens: frozenset[str]) -> frozenset[str]:
    """Return stop tokens that require operator ping per ping table."""
    return tokens & _PING_STOPS


def validate_conductor_closeout(
    body: str,
    *,
    require_mode_b_proof: bool = False,
) -> CloseoutStopVerdict:
    """Validate closeout stop vocabulary + optional Mode B admit-proof."""
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
    resume = resume_row_from_closeout(body)
    return CloseoutStopVerdict(
        ok=True,
        resume_row=resume,
        pings_required=pings_for_stops(parsed.tokens),
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
