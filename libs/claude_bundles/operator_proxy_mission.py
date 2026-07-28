"""Operator-proxy mission prompt ensure — seat map + skill chips.

When cursor launches a CDP Opus mission (``purpose`` in
``OPERATOR_PROXY_MISSION_PURPOSES``), the sealed prompt MUST open with
Claude-slug skill chips and an explicit Opus-operator / Fable-advisor
briefing. Idempotent: already-prefixed prompts are left intact aside from
injecting a missing briefing block after the slash header.
"""

from __future__ import annotations

import re

from claude_bundles.cowork_skill_delivery import (
    format_cdp_slash_prefix,
    split_leading_slash_skills,
)

OPERATOR_PROXY_MISSION_PURPOSES: frozenset[str] = frozenset(
    {"operator-proxy", "mission", "operator_proxy"}
)

MISSION_SKILL_SLUGS: tuple[str, ...] = (
    "cdp-operator-proxy",
    "reasoning-posture",
)

_BRIEFING_MARKER = "## Mission seat map (BINDING"
_BRIEFING_BLOCK = """\
## Mission seat map (BINDING — operator-proxy mission)

| Seat | Role |
|---|---|
| **CDP Opus (this seat)** | **Operator** — DIRECTIVE / DISPOSITION on a private `agent_bus.request` lane; cite endeavor root in `arc:` only |
| **CDP Fable** | **Advisor** — escalate via `team_dispatch(model=cdp/fable)` (`project_ask` escape only); Opus may fire Fable without paging Kaywan |
| **cursor-auto → nested cursor-sdk** | **Executor** — B1 direct nest under Auto lease, or B2 mint+release for tick admit (`nest_under` when gate shared — silence ⇒ stall) |
| **charter-runner tick** | **Sole admitter** for enrolled roots — mint+`enroll_rows` belt path; Auto does not improvise tip enqueue |

Work posting SOT: `cortex://notes/system/specs/cursor-auto-tick-work-posting.md`

Complete without paging Kaywan unless Fable returns `ESCALATE` + `minimal_question`, or a true operator-only gate.
"""


def is_operator_proxy_mission_purpose(purpose: str | None) -> bool:
    """True when purpose tags an operator-proxy / mission launch."""
    return (purpose or "").strip().lower() in OPERATOR_PROXY_MISSION_PURPOSES


def ensure_operator_proxy_mission_prompt(text: str) -> str:
    """Ensure slash skill chips + mission seat-map briefing on *text*.

    Idempotent. Non-mission callers should not invoke this.
    """
    body = (text or "").strip()
    tokens, rest = split_leading_slash_skills(body)
    have = {t.lstrip("/").strip().lower() for t in tokens if t.strip()}
    need = [s for s in MISSION_SKILL_SLUGS if s not in have]
    ordered: list[str] = []
    seen: set[str] = set()
    for slug in [t.lstrip("/").strip() for t in tokens if t.strip()] + need:
        key = slug.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(slug)

    prefix = format_cdp_slash_prefix(ordered)
    rest_body = rest.lstrip("\n")
    if _BRIEFING_MARKER not in rest_body:
        rest_body = f"{_BRIEFING_BLOCK.strip()}\n\n{rest_body}".rstrip() + "\n"
    # format_cdp_slash_prefix ends with \\n per slug; keep one blank line before body.
    return f"{prefix}\n{rest_body}"


_PURPOSE_DOC = re.compile(
    r"purpose\s*[:=]\s*(operator-proxy|mission|operator_proxy)",
    re.IGNORECASE,
)


def purpose_implies_mission(purpose: str | None, prompt: str | None = None) -> bool:
    """True when purpose or prompt body declares an operator-proxy mission."""
    if is_operator_proxy_mission_purpose(purpose):
        return True
    if prompt and _PURPOSE_DOC.search(prompt):
        return True
    return False


__all__ = [
    "MISSION_SKILL_SLUGS",
    "OPERATOR_PROXY_MISSION_PURPOSES",
    "ensure_operator_proxy_mission_prompt",
    "is_operator_proxy_mission_purpose",
    "purpose_implies_mission",
]
