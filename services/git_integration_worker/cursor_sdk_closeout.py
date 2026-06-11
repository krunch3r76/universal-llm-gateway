"""Closeout validation helpers for cursor-sdk worker dispatches."""

from __future__ import annotations

import re
from dataclasses import dataclass

_IMPLEMENT_PREAMBLE = (
    "Execute this task NOW using your tools. Make the code/file changes the packet "
    "specifies. If you are blocked, reply with `status: blocked` and the specific "
    "reason. Do NOT reply with an acknowledgement-only message.\n\n"
    "Before any fs write: read fs(cortex, agent-skills/architecture-invariants.md) and "
    "fs(cortex, agent-skills/ulg-architecture.md); also load any additional cortex "
    "skills named in <invariants>. Engineering-discipline rules (SLOC, scope, logging) "
    "auto-load via setting_sources; the architecture layer (topology_ws, event contracts, "
    "domain routing) is description-gated and does NOT reliably attach without these reads."
)

_CONTRACT_FRONTMATTER_RE = re.compile(
    r"^contract:\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class SdkRunOutcome:
    body: str
    status: str
    duration_ms: int
    tool_call_count: int


def count_tool_calls(turns: list) -> int:
    total = 0
    for turn in turns:
        steps = getattr(getattr(turn, "turn", None), "steps", ()) or ()
        total += sum(1 for step in steps if getattr(step, "type", "") == "toolCall")
    return total


def degraded_implement_reason(outcome: SdkRunOutcome) -> str | None:
    """Return a machine reason when an implement closeout must not claim success."""
    if outcome.status != "finished":
        return f"run_status={outcome.status}"
    if outcome.tool_call_count == 0:
        return "zero_tool_calls"
    return None


def infer_contract_from_text(text: str) -> str | None:
    match = _CONTRACT_FRONTMATTER_RE.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


def resolve_prompt_preamble(
    *,
    handoff_contract: str | None,
    prompt_preamble: str | None,
    inferred_contract: str | None,
) -> str:
    contract = (handoff_contract or inferred_contract or "consult").lower()
    if prompt_preamble:
        return f"{prompt_preamble.strip()}\n\n"
    if contract == "implement":
        return f"{_IMPLEMENT_PREAMBLE}\n\n"
    return ""


def format_closeout_body(outcome: SdkRunOutcome, degraded_reason: str | None) -> str:
    if degraded_reason:
        return f"status: degraded\nreason: {degraded_reason}\n\n{outcome.body}"
    return outcome.body
