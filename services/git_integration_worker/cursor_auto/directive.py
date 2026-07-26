"""Parse operator ``TYPE: DIRECTIVE`` bodies from agent_bus.request."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedDirective:
    """Minimal fields extracted from an operator directive body."""

    turn_type: str
    density: str | None
    raw_body: str


_TYPE_RE = re.compile(r"^TYPE:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_DENSITY_RE = re.compile(r"^density:\s*(\S+)", re.MULTILINE | re.IGNORECASE)


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
    return ParsedDirective(turn_type=turn_type, density=density, raw_body=text)


def build_sdk_message(job_body: str, *, contract: str) -> str:
    """Prompt text handed to nested cursor-sdk dispatch."""
    return (
        "Nested cursor-sdk dispatch from cursor-auto (operator proxy).\n"
        f"contract={contract}\n\n"
        f"{job_body.strip()}\n"
    )
