"""Packet contract/source-ref parsing and prompt preamble assembly for cursor-sdk.

Who calls: GIW ``_resolve_prompt`` and Stargate cursor-sdk generate — every
``team_dispatch(op=generate, seat=cursor-sdk)`` worker, including Auto nested
POSTs. Not Auto/life-only. Invariant: non-mechanical contracts get a
``reasoning-posture`` invoke unless the packet already carries one;
mechanical/quick contracts skip.
"""

from __future__ import annotations

import re

from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS

_LARGE_CONTENT_CHUNK_CHARS = 40_000

_DELIVERABLE_ROUTING_PREAMBLE = (
    "DURABLE DELIVERABLE ROUTING (mandatory): Write task deliverables — sidecars, "
    "reviews, specs — to one of the two durable shares, using the exact path from "
    "your packet <mcp_capabilities> or files_expected:\n"
    '  - cortex share via fs(op="write", path="cortex://...") — cortex:// scheme.\n'
    '  - workspaces share via fs(op="write", path="workspaces://{repo}/...") — '
    "workspaces:// scheme.\n"
    "NEVER write deliverables to /tmp/summaries/, tmp/summaries/, or tmp/reviews/ "
    "(the worker auto-writes the closeout receipt at "
    "tmp/reviews/closeouts/<dispatch_id>.md — you do not author that). "
    "Project tmp/ and /tmp/summaries/ are ephemeral scratch only, never durable output.\n\n"
    "LARGE CONTENT — CHUNK, NEVER INLINE ONE GIANT WRITE (mandatory, friction 21654): "
    f"if the content you are about to write exceeds {_LARGE_CONTENT_CHUNK_CHARS:,} "
    "characters, do NOT pass it all as one write call's content argument — a single "
    "oversized content argument can silently fail to transmit its real payload (the "
    "call reports success but the file ends up empty or truncated, with no error). "
    f"Instead split it into chunks of at most {_LARGE_CONTENT_CHUNK_CHARS:,} characters "
    'each: write the first chunk with op="write", then write each remaining chunk in '
    'order with op="append" to the same path. Verify the final file size afterward '
    "(fs read or list) before reporting the deliverable done.\n\n"
    "CORTEX READ-MODIFY-WRITE (mandatory): when patching an existing cortex:// "
    'artifact, fs(op="read") first and pass expected_sha256=<read_sha256 from the '
    "read response> on overwrite/replace/append — stale or partial reads fail closed "
    "instead of silently truncating durable binds."
)

_BREADTH_RECON_PREAMBLE = (
    "BREADTH RECON — EXPLORE DEFAULT (mandatory when owed):\n"
    "When breadth recon is owed — loci unknown, question spans ≥3 files or an "
    "unfamiliar subsystem, you are about a 2nd speculative Grep/Glob round, or "
    "the packet contract is investigate/light-bounded recon — your default read "
    'move is Task(subagent_type="explore", …). Explore is the Cursor subagent '
    "(¬ in-seat Grep spray, ¬ a separate tool).\n"
    "Anti-triggers (in-seat Grep/Read OK): loci known (path in hand ∨ one grep "
    "away); you need file contents verbatim because you are about to edit them; "
    "latency-sensitive tight-loop debugging.\n"
    "Closeout MUST include a recon_method line: "
    "`recon_method: explore` | `recon_method: in-seat` + one-line anti-trigger reason "
    "| `recon_method: waived` + packet cite. Omitting recon_method when breadth recon "
    "was owed is a scope-delta defect the relay may flag (advisory)."
)

_LANE_B_REPO_EDIT_PREAMBLE = (
    "LANE-B REPO EDITS (mandatory): Make repository source edits with native file "
    "tools (Read, Write, StrReplace) in your isolated worktree — not via MCP "
    'fs(op="write"|"append"|"replace"|...) on workspaces:// paths (those writes '
    "hard-fail capture). Use cortex:// via fs only for durable deliverables "
    "(sidecars, specs, reviews)."
)

_LANE_B_BRANCH_CONTRACT_TEMPLATE = (
    "LANE-B BRANCH CONTRACT (mandatory): Your commits land on {branch}. That branch "
    "is yours to retire — a lane that walks away from it leaves an attributed debt "
    "against this thread, visible to whoever dispatches here next.\n"
    "Declare the outcome in your closeout as a `land_disposition:` line:\n"
    "  - `land_disposition: landed` — the work is on master. Verified by content "
    "probe, not by assertion: a claim the tree does not show is refused and names "
    "the paths that disagree.\n"
    "  - `land_disposition: discard` + `land_reason: <why>` — deliberately "
    "abandoned. A recorded reason is a complete, honest outcome.\n"
    "Omitting the line while your branch carries commits master lacks grades the "
    "closeout `partial` with `land:lane_b_unlanded` and opens the debt. Both "
    "declared outcomes archive the tip first, so neither loses work."
)

_IMPLEMENT_PREAMBLE = (
    "Execute this task NOW using your tools. Make the code/file changes the packet "
    "specifies. If you are blocked, reply with `status: blocked` and the specific "
    "reason. Do NOT reply with an acknowledgement-only message.\n\n"
    "Before any fs write: Use the `architecture-invariants` skill, "
    "Use the `ulg-architecture` skill, and Use the `docstring-quality` skill "
    "(canonical slugs — seat self-fetches; ¬ fs-read "
    "skill paths); also use any additional skills named in <invariants>. "
    "Engineering-discipline rules (SLOC, scope, logging) "
    "auto-load via setting_sources; the architecture layer (topology_ws, event contracts, "
    "domain routing) is description-gated and does NOT reliably attach without these uses.\n\n"
    "Do NOT post your result to the agent-bus yourself — the worker delivers your "
    "closeout automatically. Produce your result as your final message only."
)

# Cursor-sdk analog of CDP ``ensure_cdp_judgment_skills`` — prompt invoke, not
# ``skills=`` mount (cursor-sdk skips skills_mount). Mechanical/quick skip.
_REASONING_POSTURE_PREAMBLE = (
    "Use the `reasoning-posture` skill — pin Question/OOS/detent before merits; "
    "steelman / calibrate / courage; thinking_off does not waive."
)

# Shared with Stargate ``handoff_reasoning_posture.REASONING_POSTURE_SKIP_CONTRACTS``.
_REASONING_POSTURE_SKIP_CONTRACTS = REASONING_POSTURE_SKIP_CONTRACTS
_REASONING_POSTURE_INVOKE_RE = re.compile(
    r"Use the `?reasoning-posture`? skill",
    re.IGNORECASE,
)

_CONTRACT_FRONTMATTER_RE = re.compile(
    r"^contract:\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def infer_contract_from_text(text: str) -> str | None:
    """Return the ``contract:`` frontmatter token from *text*, or None if absent."""
    match = _CONTRACT_FRONTMATTER_RE.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


_SOURCE_REF_FRONTMATTER_RE = re.compile(
    r"^source_ref:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_WORK_ITEM_KEY_RE = re.compile(
    r"^(?:todo|plan|plan_phase|packet):\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_WORK_ITEM_SCHEMES = ("todo:", "plan:", "plan_phase:", "packet:", "agent-bus:")


def extract_source_ref_from_packet(text: str) -> str | None:
    """Canonical work-item source_ref from packet frontmatter, or None.

    Prefers an explicit ``source_ref:`` line; else a ``todo:``/``plan:``/
    ``plan_phase:``/``packet:`` frontmatter line (value is already
    scheme-qualified, e.g. ``todo: todo:x``). Returns None when no
    scheme-qualified work-item ref is present (e.g. message dispatch).
    """
    for pattern in (_SOURCE_REF_FRONTMATTER_RE, _WORK_ITEM_KEY_RE):
        match = pattern.search(text)
        if match:
            ref = match.group(1).strip()
            if ref.startswith(_WORK_ITEM_SCHEMES):
                return ref
    return None


def _already_invokes_reasoning_posture(*texts: str | None) -> bool:
    """True when any text already carries the Cursor invoke cue."""
    return any(bool(t) and _REASONING_POSTURE_INVOKE_RE.search(t) for t in texts)


def resolve_prompt_preamble(
    *,
    handoff_contract: str | None,
    prompt_preamble: str | None,
    inferred_contract: str | None,
    lane: str | None = None,
    existing_text: str | None = None,
    lane_branch: str | None = None,
) -> str:
    """Assemble the worker prompt prefix for one cursor-sdk dispatch.

    Non-mechanical contracts get a ``reasoning-posture`` invoke line unless
    *prompt_preamble* or *existing_text* already carries one (idempotent).

    Lane-B dispatches additionally carry the branch contract: the obligation to
    declare a land disposition arrives with the work rather than after residue
    already exists.
    """
    contract = (handoff_contract or inferred_contract or "consult").lower()
    if prompt_preamble:
        preamble = prompt_preamble.strip()
    elif contract == "implement":
        preamble = _IMPLEMENT_PREAMBLE
    else:
        preamble = ""

    parts = [_DELIVERABLE_ROUTING_PREAMBLE, _BREADTH_RECON_PREAMBLE]
    if lane == "B":
        parts.append(_LANE_B_REPO_EDIT_PREAMBLE)
        parts.append(
            _LANE_B_BRANCH_CONTRACT_TEMPLATE.format(
                branch=lane_branch or "your lane branch"
            )
        )
    if (
        contract not in _REASONING_POSTURE_SKIP_CONTRACTS
        and not _already_invokes_reasoning_posture(prompt_preamble, existing_text)
    ):
        parts.append(_REASONING_POSTURE_PREAMBLE)
    if preamble:
        parts.append(preamble.strip())
    return "\n\n".join(parts) + "\n\n"
