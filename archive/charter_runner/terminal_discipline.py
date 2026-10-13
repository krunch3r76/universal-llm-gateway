"""Terminal discipline for state-advancing charter dispatches.

A gated step cannot be marked done without a caller-observable terminal: a
resolvable proof/evidence URI or an explicit typed terminal-error code.
Unresolvable evidence URIs fail closed — silence is not success.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_URI_RE = re.compile(
    r"(?:cortex|workspaces|agent-bus|file)://[^\s)\]\>,]+",
    re.IGNORECASE,
)
_TERMINAL_ERROR_RE = re.compile(
    r"\b(?:terminal[_-]error|TERMINAL_ERROR)\s*[:\-—]\s*(\S+)",
    re.IGNORECASE,
)

UriResolver = Callable[[str], bool]


@dataclass(frozen=True)
class TerminalCheck:
    """Outcome of a terminal-evidence check."""

    ok: bool
    reason: str
    code: str | None = None


def extract_evidence_uris(text: str) -> list[str]:
    """Return share/agent-bus URIs found in ``text``."""
    return _URI_RE.findall(text or "")


def extract_terminal_error(text: str) -> str | None:
    """Return an explicit terminal-error token when present."""
    match = _TERMINAL_ERROR_RE.search(text or "")
    if match is None:
        return None
    return match.group(1).strip().rstrip(".,)")


def _default_uri_resolvable(uri: str) -> bool:
    """Shape-only resolver for offline/unit paths (non-empty path segment)."""
    if "://" not in uri:
        return False
    _scheme, rest = uri.split("://", 1)
    path = rest.split("#", 1)[0].strip()
    return bool(path) and path not in {"/"}


def has_resolvable_terminal(
    evidence: str | None,
    *,
    terminal_error: str | None = None,
    uri_resolver: UriResolver | None = None,
) -> TerminalCheck:
    """True when evidence carries a resolvable URI or explicit terminal error."""
    err = (terminal_error or "").strip() or extract_terminal_error(evidence or "")
    if err:
        return TerminalCheck(True, "terminal_error", code=err)
    resolve = uri_resolver or _default_uri_resolvable
    uris = extract_evidence_uris(evidence or "")
    if not uris:
        return TerminalCheck(
            False,
            "no_terminal_evidence",
            code="terminal_missing",
        )
    if any(resolve(uri) for uri in uris):
        return TerminalCheck(True, "evidence_uri")
    return TerminalCheck(
        False,
        "terminal_evidence_unresolved",
        code="terminal_unresolved",
    )


def gated_step_done_allowed(
    *,
    step_status: str,
    terminal_evidence: str | None,
    terminal_error: str | None = None,
    uri_resolver: UriResolver | None = None,
) -> TerminalCheck:
    """A gated step marked ``done`` requires resolvable terminal proof."""
    if step_status != "done":
        return TerminalCheck(True, "not_done")
    return has_resolvable_terminal(
        terminal_evidence,
        terminal_error=terminal_error,
        uri_resolver=uri_resolver,
    )


__all__ = [
    "TerminalCheck",
    "extract_evidence_uris",
    "extract_terminal_error",
    "gated_step_done_allowed",
    "has_resolvable_terminal",
]
