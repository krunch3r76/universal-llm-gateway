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
    return ParsedDirective(
        turn_type=turn_type,
        density=density,
        require_attended=require_attended,
        raw_body=text,
    )


def build_sdk_message(job_body: str, *, contract: str) -> str:
    """Prompt text handed to nested cursor-sdk dispatch."""
    return (
        "Nested cursor-sdk dispatch from cursor-auto (operator proxy).\n"
        f"contract={contract}\n\n"
        f"{job_body.strip()}\n"
    )
