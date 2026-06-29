"""Mechanical dense-spec schema validator (structural presence + fork closure)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_REQUIRED_SECTIONS: dict[str, re.Pattern[str]] = {
    "problem": re.compile(r"^#{1,6}\s.*\bproblem\b", re.I | re.M),
    "non_goals": re.compile(r"^#{1,6}\s.*(non-?goal|scope exclusion)", re.I | re.M),
    "provenance": re.compile(
        r"^#{1,6}\s.*(source[- ]of[- ]truth|provenance)", re.I | re.M
    ),
    "touch_points": re.compile(r"^#{1,6}\s.*(touch[- ]?point|touchpoint)", re.I | re.M),
    "forks": re.compile(
        r"^#{1,6}\s.*(bound design|fork table|design decision|resolved fork)",
        re.I | re.M,
    ),
    "implementation": re.compile(
        r"^#{1,6}\s.*(implementation guidance|implementation steps)", re.I | re.M
    ),
    "acceptance": re.compile(r"^#{1,6}\s.*\bacceptance\b", re.I | re.M),
    "verification": re.compile(r"^#{1,6}\s.*(verification|quality gate)", re.I | re.M),
}
_REASONING_TRACE_RE = re.compile(
    r"<reasoning_trace>(.*?)</reasoning_trace>", re.I | re.S
)
_OPEN_FORK_RE = re.compile(r"\bOPEN\s*:", re.I)
_ATTESTATION_RE = re.compile(r"no\s+fork\s+remains\s+open", re.I)
_FENCE_RE = re.compile(r"(```|~~~).*?\1", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_LINE_ANCHORED_FENCE_RE = re.compile(r"^(?:```|~~~)", re.MULTILINE)

# Per-section accepted-pattern hints for diagnostic messages.  The validator
# matches keyword-anchored regexes (above), NOT the literal key names, so the
# canonical section key alone is insufficient for an author to know what heading
# text will pass.  Emit these alongside the missing-key list (friction 21176).
_SECTION_ACCEPTED_PATTERNS: dict[str, str] = {
    "problem": "heading containing 'problem'",
    "non_goals": "heading containing 'non-goal' or 'scope exclusion'",
    "provenance": "heading containing 'source-of-truth' or 'provenance'",
    "touch_points": "heading containing 'touch-point' or 'touchpoint'",
    "forks": (
        "heading containing one of: 'bound design', 'fork table', "
        "'design decision', 'resolved fork'"
    ),
    "implementation": (
        "heading containing 'implementation guidance' or 'implementation steps'"
    ),
    "acceptance": "heading containing 'acceptance'",
    "verification": "heading containing 'verification' or 'quality gate'",
    "reasoning_trace": (
        "<reasoning_trace>…</reasoning_trace> tag block (not a heading)"
    ),
    "reasoning_trace_attestation": (
        "<reasoning_trace> body must contain 'no fork remains open'"
    ),
}
DENSE_SPEC_RE = re.compile(
    r"(?:tasks/specs|notes/system/specs)/[^/\s#?]+\.md", re.IGNORECASE
)


def spec_basename(uri: str) -> str | None:
    """Return the slug filename from a dense-spec URI, or None if uncited."""
    match = DENSE_SPEC_RE.search(uri)
    if not match:
        return None
    return PurePosixPath(match.group(0)).name


@dataclass(frozen=True, slots=True)
class DenseSpecVerdict:
    passed: bool
    code: str | None = None
    reason: str | None = None
    missing_sections: tuple[str, ...] = ()
    open_fork_markers: int = 0


def _strip_code(text: str) -> str:
    """Remove fenced blocks + inline code spans before structural checks."""
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))


def _has_stray_fence(text: str) -> bool:
    """True when ``` or ~~~ appears inside a line (not only at line start).

    _FENCE_RE uses re.S (DOTALL), so a mid-line triple-backtick sequence can
    act as a fence opener and swallow subsequent headings.  An uneven count
    between all occurrences and line-anchored occurrences is a reliable signal.
    """
    all_count = len(re.findall(r"```|~~~", text))
    anchored_count = len(_LINE_ANCHORED_FENCE_RE.findall(text))
    return all_count > anchored_count


def dense_spec_sha256(text: str) -> str:
    """Bare lowercase hex digest of the spec bytes (no prefix)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dense_spec_hash_uri(text: str) -> str:
    """Canonical evidence token cited by the implement-ready assertion."""
    return f"spec_sha256:{dense_spec_sha256(text)}"


def validate_dense_spec(text: str) -> DenseSpecVerdict:
    """Mechanical: ALL structural checks run on code-stripped visible text."""
    visible = _strip_code(text)
    missing = tuple(
        name for name, rx in _REQUIRED_SECTIONS.items() if not rx.search(visible)
    )
    trace = _REASONING_TRACE_RE.search(visible)
    trace_body = trace.group(1) if trace else ""
    if trace is None or not trace_body.strip():
        missing = (*missing, "reasoning_trace")
    elif not _ATTESTATION_RE.search(trace_body):
        missing = (*missing, "reasoning_trace_attestation")
    open_markers = len(_OPEN_FORK_RE.findall(visible))
    if missing:
        hints = "; ".join(
            f"{k}: {_SECTION_ACCEPTED_PATTERNS[k]}"
            for k in missing
            if k in _SECTION_ACCEPTED_PATTERNS
        )
        stray_hint = (
            " — note: stray non-line-start ``` or ~~~ may have caused headers"
            " to be misread as inside a code block"
            if _has_stray_fence(text)
            else ""
        )
        reason = (
            f"missing required sections: {', '.join(missing)}"
            + (f" — accepted patterns: {hints}" if hints else "")
            + stray_hint
        )
        return DenseSpecVerdict(
            False,
            "dense_spec_sections_missing",
            reason,
            missing,
            open_markers,
        )
    if open_markers:
        return DenseSpecVerdict(
            False,
            "dense_spec_open_forks",
            f"{open_markers} unresolved OPEN fork marker(s)",
            (),
            open_markers,
        )
    return DenseSpecVerdict(True)


__all__ = [
    "DENSE_SPEC_RE",
    "DenseSpecVerdict",
    "dense_spec_hash_uri",
    "dense_spec_sha256",
    "spec_basename",
    "validate_dense_spec",
]
