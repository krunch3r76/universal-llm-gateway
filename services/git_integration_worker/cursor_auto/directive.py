"""Parse operator ``TYPE: DIRECTIVE`` bodies from agent_bus.request."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_auto.queue import AutoJob


@dataclass(frozen=True, slots=True)
class ParsedDirective:
    """Minimal fields extracted from an operator directive body."""

    turn_type: str
    density: str | None
    require_attended: bool
    evidence_required_uris: tuple[str, ...]
    raw_body: str


_TYPE_RE = re.compile(r"^TYPE:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_DENSITY_RE = re.compile(r"^density:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_REQUIRE_ATTENDED_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?require_attended:\s*(true|yes|1)\b",
    re.MULTILINE | re.IGNORECASE,
)
_EXECUTOR_BIND_ATTENDED_RE = re.compile(
    r"^[ \t]*executor_bind:\s*attended\b",
    re.MULTILINE | re.IGNORECASE,
)
_EVIDENCE_REQUIRED_RE = re.compile(
    r"^[ \t]*evidence_required\s*:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_READ_CORPUS_RE = re.compile(
    r"^[ \t]*(?:read[- ]corpus|corpus|read)\s*:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_URI_TOKEN_RE = re.compile(r"(?:cortex|workspaces)://[^\s`>,]+")
_SHA_SUFFIX_RE = re.compile(
    r"\s*[·•]\s*sha256:.*$|\s+sha256:[0-9a-f]+.*$",
    re.IGNORECASE,
)


def _clean_uri_token(raw: str) -> str | None:
    """Strip backticks, trailing punctuation, and sha suffixes from a URI token."""
    token = raw.strip().strip("`\"'").rstrip(".,;:)>")
    token = _SHA_SUFFIX_RE.sub("", token).strip()
    if token.lower().startswith(("cortex://", "workspaces://")):
        return token
    return None


def _harvest_uri_tokens(line: str) -> list[str]:
    """Collect durable-share URIs from one directive line."""
    uris: list[str] = []
    seen: set[str] = set()
    for match in _URI_TOKEN_RE.finditer(line):
        cleaned = _clean_uri_token(match.group(0))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            uris.append(cleaned)
    return uris


def _extract_evidence_required_uris(text: str) -> tuple[str, ...]:
    """Parse ``evidence_required`` and labeled read-corpus lines for durable URIs."""
    ordered: list[str] = []
    seen: set[str] = set()
    for pattern in (_EVIDENCE_REQUIRED_RE, _READ_CORPUS_RE):
        for match in pattern.finditer(text):
            for uri in _harvest_uri_tokens(match.group(1)):
                if uri not in seen:
                    seen.add(uri)
                    ordered.append(uri)
    return tuple(ordered)


def _extract_guard_line_uris(text: str) -> tuple[str, ...]:
    """Parse only ``evidence_required:`` lines — confer fence guard source."""
    ordered: list[str] = []
    seen: set[str] = set()
    for match in _EVIDENCE_REQUIRED_RE.finditer(text):
        for uri in _harvest_uri_tokens(match.group(1)):
            if uri not in seen:
                seen.add(uri)
                ordered.append(uri)
    return tuple(ordered)


def corpus_guard_uris(directive: ParsedDirective | None) -> frozenset[str]:
    """Return cortex guard URIs for confer write-fence intersection (v1 cortex-only).

    Guard scope is limited to ``evidence_required:`` lines (not read-corpus harvest).
    ``workspaces://`` tokens on those lines are collected for prompt inject only.
    """
    if directive is None:
        return frozenset()
    guard_line_uris = _extract_guard_line_uris(directive.raw_body)
    return frozenset(
        uri for uri in guard_line_uris if uri.lower().startswith("cortex://")
    )


def _body_requires_attended(text: str) -> bool:
    """True when DIRECTIVE body declares attended executor bind."""
    return bool(
        _REQUIRE_ATTENDED_RE.search(text) or _EXECUTOR_BIND_ATTENDED_RE.search(text)
    )


def effective_require_attended(
    job: AutoJob,
    directive: ParsedDirective | None,
) -> bool:
    """OR wire + body attendance signals for handler short-circuit."""
    return bool(job.require_attended) or (
        directive is not None and directive.require_attended
    )


def attendance_surface(
    job: AutoJob,
    directive: ParsedDirective | None,
) -> str | None:
    """Which surface set attendance: ``wire``, ``body``, ``both``, or ``None``."""
    wire = bool(job.require_attended)
    body = directive is not None and directive.require_attended
    if wire and body:
        return "both"
    if wire:
        return "wire"
    if body:
        return "body"
    return None


def parse_request_body(body: str) -> ParsedDirective | None:
    """Return parsed directive when body declares ``TYPE: DIRECTIVE``."""
    text = (body or "").strip()
    if not text:
        return None
    match = _TYPE_RE.search(text)
    if match is None:
        return None
    turn_type = match.group(1).strip().upper()
    if turn_type != "DIRECTIVE":
        return None
    density_match = _DENSITY_RE.search(text)
    density = density_match.group(1).strip().lower() if density_match else None
    require_attended = _body_requires_attended(text)
    evidence_required_uris = _extract_evidence_required_uris(text)
    return ParsedDirective(
        turn_type=turn_type,
        density=density,
        require_attended=require_attended,
        evidence_required_uris=evidence_required_uris,
        raw_body=text,
    )


def build_sdk_message(job_body: str, *, contract: str) -> str:
    """Prompt text handed to nested cursor-sdk dispatch."""
    body = job_body.strip()
    lines = [
        "Nested cursor-sdk dispatch from cursor-auto (operator proxy).",
        f"contract={contract}",
        "",
        body,
    ]
    if contract == "confer":
        directive = parse_request_body(body)
        guard = sorted(corpus_guard_uris(directive))
        if guard:
            lines.extend(
                [
                    "",
                    "## Confer write fence (mandatory)",
                    "Forbidden durable write targets for this episode:",
                    *(f"- {uri}" for uri in guard),
                    "",
                    "Allowed alternatives:",
                    "- Write a distinct report sibling (e.g. …/confer-report.md) and "
                    "announce it in effects + deltas_to_spec, or",
                    "- Emit an explicit line: fence_exception: <uri> — <reason>",
                    "",
                    "Emit §2 fields inline in your closeout (ac_verdict, deltas_to_spec, "
                    "effects, etc.).",
                ]
            )
    return "\n".join(lines) + "\n"
