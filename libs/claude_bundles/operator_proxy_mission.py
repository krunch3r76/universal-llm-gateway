"""Operator-proxy mission prompt ensure — seat map + skill chips.

When cursor launches a CDP Opus mission (``purpose`` in
``OPERATOR_PROXY_MISSION_PURPOSES``), the sealed prompt MUST open with
Claude-slug skill chips and an explicit Opus-operator / Fable-advisor
briefing. Idempotent: already-prefixed prompts are left intact aside from
injecting a missing briefing block after the slash header.
"""

from __future__ import annotations

import re

from claude_bundles.act_receipt import format_act_receipt
from claude_bundles.cowork_skill_delivery import (
    format_cdp_slash_prefix,
    split_leading_slash_skills,
)
from claude_bundles.operator_proxy_tier_m import tier_m_authoring_block

# Superset of tier-M consumers for mission lands touching shared bundles.
CONSUMERS: tuple[str, ...] = ("mcp",)

OPERATOR_PROXY_MISSION_PURPOSES: frozenset[str] = frozenset(
    {"operator-proxy", "mission", "operator_proxy"}
)

# Substantive operator seat: scope rails + epistemic quality stay paired
# (decision:reasoning-frontier-skill-pair).
MISSION_SKILL_SLUGS: tuple[str, ...] = (
    "cdp-operator-proxy",
    "reasoning-posture",
    "frontier-reasoning-discipline",
)

# Derived from config/mcp/canonical.yaml surface_primary_domains.life (A9).
LIFE_SURFACE_LEGAL_TOOLS: frozenset[str] = frozenset(
    {
        "cortex",
        "cortex_brief",
        "agent_bus",
        "agent_bus_read",
        "fs",
        "rag",
        "retrieve",
        "tool_search",
        "dispatch",
        "imprint",
        "delegate",
        "notify",
    }
)

# CODE_EXTRA on /mcp/code — forbidden as direct life-seat tool calls (A9).
# Keep aligned with endpoint_surface.derive_code_extra_primary_tools() (includes
# project_ask while code-primary; escape-only transport — consult-routing § Surface gate).
LIFE_SURFACE_FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "team_dispatch",
        "manage",
        "observability",
        "panel_dispatch",
        "pipeline",
        "project_ask",
    }
)

_BRIEFING_MARKER = "## Mission seat map (BINDING"
_FORBIDDEN_HEADING = "## Life surface — FORBIDDEN verbs (BINDING)"
_ACT_RECEIPT_HEADING = "## ACT-RECEIPT (BINDING — trigger-fired / mission closeout)"


def _legal_tools_line() -> str:
    return ", ".join(f"`{t}`" for t in sorted(LIFE_SURFACE_LEGAL_TOOLS))


def _forbidden_tools_line() -> str:
    return ", ".join(f"`{t}`" for t in sorted(LIFE_SURFACE_FORBIDDEN_TOOLS))


def _receipt_example() -> str:
    return format_act_receipt(
        commission_kind="agent_bus_request",
        evidence_uri="cortex://notes/system/ephemeral/example/act-evidence.md",
        trigger_id="example-trigger-id",
    )


def _build_briefing_block() -> str:
    receipt_example = _receipt_example()
    tier_m_block = tier_m_authoring_block()
    return f"""\
## Mission seat map (BINDING — operator-proxy mission)

| Seat | Role |
|---|---|
| **CDP Opus (this seat)** | **Operator** — DIRECTIVE / DISPOSITION on a private `agent_bus.request` lane; cite endeavor root in `arc:` only |
| **CDP Fable** | **Advisor** — escalate via **`agent_bus.request`** to a code-seat consult thread (life-reachable); code-surface tools are **not** callable from this life seat |
| **cursor-auto → nested cursor-sdk** | **Executor** — B1 direct nest under Auto lease, or B2 mint+release for tick admit (`nest_under` when gate shared — silence ⇒ stall). Address it as `to="cursor"` via `agent_bus.request`. |
| **charter-runner** | **Sole launcher** for enrollments — mint+`enroll_rows` belt path; Auto does not improvise tip enqueue |

## Life surface act path (BINDING)

Legal verbs on `/mcp/life` (mechanized from `surface_primary_domains.life`): {_legal_tools_line()}.

Act on code-surface capabilities only by **commissioning** through life-reachable paths:
`agent_bus.request` with `to="cursor"` — arms cursor-auto (B1/B2); `cursor-auto` is the executor role, ¬ a bus address — or charter enroll (`enroll_charter_runner=true`).

{_FORBIDDEN_HEADING}

Never invoke these as life-seat MCP tools (they live on `/mcp/code` only): {_forbidden_tools_line()}.

Within the seat-map / escalate section above, the Fable escalate verb is **`agent_bus`**, never life-tool `team_dispatch`.

{tier_m_block}

{_ACT_RECEIPT_HEADING}

Before CLOSEOUT on trigger-fired or operator-proxy mission work, emit an ACT-RECEIPT fence
(grammar SOT: `libs/claude_bundles/act_receipt.py`). Shipped `commission_kind` values:
`agent_bus_request`, `charter_enroll` only.

Example (must parse via `parse_act_receipt`):

{receipt_example}

**Mission default (BINDING — 2026-07-28):** `idea → bind → implement at will → live autonomy`.
After the architecture bind (and Fable when needed), this seat **commissions cursor-auto
implement** via `agent_bus.request` without waiting for Kaywan ratification or a
separate IDE helm turn — unless the mission packet **explicitly** scopes implement
out. Write boundary unchanged: Opus directs; cursor-auto executes repo writes.
**Verify independently with cursor-auto** (tests, probes, health) — do not wake Kaywan
to confirm what Auto can confirm. **cursor-auto may itself be modified** when that
extends capability or effectiveness. Anti-pattern: closing at bind CLOSEOUT when ACs
are already executable, or `COME TO IDE` for ordinary progress.

Work posting SOT: `cortex://notes/system/specs/cursor-auto-tick-work-posting.md`

**Revocation (BINDING):** disable the trigger row via GIW schedule API for future fires;
revoke in-flight code-side commission lanes via operator action on the endeavor root —
do not rely on briefing prose alone.

**Claude.ai Authorize-triggers (BINDING):** scheduling triggers are an **option**.
Product may prompt Kaywan to authorize triggers (⊃ schedule-alone). **Kaywan
always Approves** — expect the click, ¬ uncertain, ¬ work around. After fire:
autonomous or ping Kaywan. SOT: `cortex://notes/system/specs/claude-ai-cowork-trigger-auth-gate.md`

**Inform Kaywan (BINDING — operator bind 2026-07-30: default ON):** two pager classes —
(1) **Awareness** — **required cadence**, not optional judgment: NL progress ping via life
MCP ``notify`` after every material CLOSEOUT, every DISPOSITION, every blocked→ask, and
every bind fork (subject must **not** say `COME TO IDE`; he need not open Cursor).
Write facts to the turn/sidecar first, then deliver. (2) **Interrupt:** subject
**`COME TO IDE`** only for **mission debrief** or when **all other options are exhausted**.
**In-session carve-out:** suppress ``notify`` only while Kaywan is in *this* Cowork CSE
chat — IDE-only presence does **not** suppress (he still wants Fi play-by-play). Do not
wait on the story-wire projector for attention. v1 delivery = life MCP `notify` (ref
required; `(unreferenced)` degrade OK). SOT: `cortex://notes/system/specs/life-mcp-story-wire-update.md`
· a:26834 · a:26841 · `cdp-operator-proxy` inv 22.

Complete without `COME TO IDE` unless mission debrief or options exhausted (Fable
`ESCALATE` + `minimal_question`, or a true operator-only gate). Authorize-triggers:
page once if away, then proceed — approval is standing.
"""


_BRIEFING_BLOCK = _build_briefing_block()


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
    "LIFE_SURFACE_FORBIDDEN_TOOLS",
    "LIFE_SURFACE_LEGAL_TOOLS",
    "MISSION_SKILL_SLUGS",
    "OPERATOR_PROXY_MISSION_PURPOSES",
    "_FORBIDDEN_HEADING",
    "ensure_operator_proxy_mission_prompt",
    "is_operator_proxy_mission_purpose",
    "purpose_implies_mission",
]
