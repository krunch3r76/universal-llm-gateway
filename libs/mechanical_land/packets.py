"""Packet rendering for mechanical-land legs (§8)."""

from __future__ import annotations

HARD_BANS_BLOCK = """## Hard bans (a:40262, git-posture § Forward only)
- Never run any form of `git stash` (stash, stash -u, stash pop, stash apply, stash drop).
- Never run `git checkout`, `git switch`, `git reset`, `git clean`, or `git restore` against the shared checkout /mnt/torus/projects/universal-llm-gateway. Your worktree is the only tree you touch.
- Never `git push --force`, never rewrite history, never cherry-pick onto master. Never push at all.
- Do not land. Do not restart services. Do not post to agent-bus except your CLOSEOUT (or, for a reviewer, your VERDICT).
- Change only the files named in files_expected. Anything else you think should change goes in your CLOSEOUT under `proposals:`.
"""

VERDICT_GRAMMAR = (
    "Post exactly one line-start canonical verdict:\n"
    "VERDICT: APPROVE range=<40hex>..<40hex> head=<40hex same as range tip>\n"
    "VERDICT: REQUEST_CHANGES range=<40hex>..<40hex> head=<40hex> BLOCKERS: <reason>\n"
    "Optional on re-review after fixes: fixes=<40hex prior reviewed head>"
)


def render_fix_packet(
    *,
    run_id: str,
    step_id: str,
    attempt: int,
    reason: str,
    gate_output: str | None = None,
    review_blockers: str | None = None,
    files_expected: list[str] | None = None,
) -> str:
    """Render a gate-fix or review-fix freeform packet."""
    header = (
        f"mechanical-land run={run_id} step={step_id} attempt={attempt} "
        f"reason={reason}\n"
    )
    body_parts = [header]
    if files_expected:
        body_parts.append(
            "files_expected:\n" + "\n".join(f"- {p}" for p in files_expected)
        )
        body_parts.append("")
    if gate_output is not None:
        body_parts.append(f"<gate_output>\n{gate_output}\n</gate_output>\n")
    if review_blockers is not None:
        body_parts.append(f"<review_blockers>\n{review_blockers}\n</review_blockers>\n")
    body_parts.append(HARD_BANS_BLOCK)
    return "\n".join(body_parts)


def render_review_packet(
    *,
    run_id: str,
    attempt: int,
    range_spec: str,
    head: str,
    branch: str | None = None,
    lane_thread: str | None = None,
    fixes: str | None = None,
    waiver_note: str | None = None,
) -> str:
    """Review packet for route (a) REQUEST and route (b) check-review prompt."""
    lines = [
        f"mechanical-land code review run={run_id} review@{attempt}",
        f"range={range_spec}",
        f"head={head}",
    ]
    if fixes:
        lines.append(f"fixes={fixes}")
    if branch:
        lines.append(f"branch={branch}")
    if lane_thread:
        lines.append(f"lane_thread={lane_thread}")
    if waiver_note:
        lines.append(waiver_note)
    lines.append("")
    lines.append(VERDICT_GRAMMAR)
    lines.append("")
    lines.append(HARD_BANS_BLOCK)
    return "\n".join(lines)


def render_evidence_packet(*, run_id: str, ask: str, read_paths: list[str]) -> str:
    lines = [
        f"mechanical-land evidence run={run_id}",
        ask.strip(),
    ]
    if read_paths:
        lines.append("")
        lines.append("read_paths:")
        lines.extend(f"- {p}" for p in read_paths)
    lines.append("")
    lines.append(HARD_BANS_BLOCK)
    return "\n".join(lines)


def packet_sha256(markdown: str) -> str:
    import hashlib

    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def review_request_body(packet_markdown: str) -> str:
    """Route (a) bus REQUEST body is the same bytes as the review packet."""
    return packet_markdown


def check_review_prompt(packet_markdown: str) -> str:
    """Route (b) check-review prompt is the same packet."""
    return packet_markdown
