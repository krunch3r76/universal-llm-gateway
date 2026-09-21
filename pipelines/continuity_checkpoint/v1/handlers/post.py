"""Compose CHECKPOINT body and post to agent-bus."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, override

from agent_bus_store.checkpoint_projection import CANONICAL_RESUME_FOOTER
from continuity_tape.events import stargate_continuity_checkpoint_posted
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._card_patch import clamp_residue
from ._clients import bus_get, bus_send, step_output_json

logger = logging.getLogger(__name__)

_MECHANICAL_PREFIX = "TYPE: CHECKPOINT · pipeline · seal facts"
_SCOREBOARD_LINE = re.compile(r"^Scoreboard:\s*\S.*$", re.MULTILINE)
_CARRIED_SUFFIX = " · carried"


def _extract_residue_block(body: str) -> str | None:
    """Return populated ## Residue body text when present on a prior tip."""
    if not body:
        return None
    try:
        from markdown_sections import read_section
    except ImportError:
        return None
    for header in (
        "Residue (authored — cap ~800 chars)",
        "Residue (authored",
        "Residue",
    ):
        try:
            text = read_section(body, header).strip()
        except Exception:  # noqa: BLE001
            continue
        if text and _MECHANICAL_PREFIX not in text:
            return text
    return None


def _carry_forward_scoreboard_pin(prior_body: str) -> str | None:
    """Reuse prior tip Scoreboard line when tail produced no live pin."""
    if not prior_body:
        return None
    match = _SCOREBOARD_LINE.search(prior_body)
    if not match:
        return None
    line = match.group(0).strip()
    # Idempotent across consecutive tail misses — marker stacks at most once.
    if line.endswith(_CARRIED_SUFFIX):
        return line
    return f"{line}{_CARRIED_SUFFIX}"


def _carry_forward_residue(*, prior_body: str, prior_turn: int) -> str | None:
    block = _extract_residue_block(prior_body)
    if not block:
        return None
    marker = (
        f"(carried forward from turn {prior_turn} — pipeline produced no residue)"
    )
    return f"{block.rstrip()}\n{marker}"


def _compose_body(
    *,
    residue: str,
    seal: dict[str, Any],
    mission: str,
    surface: str = "cursor",
    channel: str = "continuity",
) -> str:
    lines = ["## Residue (authored — cap ~800 chars)", residue.strip(), "", "## Anchor"]
    refused = seal.get("refused")
    if refused:
        lines.append(f"Harvest: refused({refused.get('code', 'unknown')})")
    else:
        transcript_id = seal.get("transcript_id") or ""
        turn_count = seal.get("turn_count") or 0
        session_id = seal.get("session_id") or ""
        sha = seal.get("messages_sha256")
        sha_token = sha if sha else "absent"
        hop_suffix = " · channel=hop" if channel == "hop" else ""
        if surface == "claude_ai":
            chat_url = seal.get("chat_url") or ""
            coverage = seal.get("coverage") or "tail"
            lines.append(
                f"Window: chat_url={chat_url} · transcript_id={transcript_id} · "
                f"turns@cp={turn_count} · coverage={coverage}{hop_suffix}"
            )
            harvest_surface = "claude_ai"
        else:
            lines.append(
                f"Window: transcript_id={transcript_id} · "
                f"turns@cp={turn_count}{hop_suffix}"
            )
            harvest_surface = "cursor"
        codec = str(seal.get("verbatim_codec") or "messages-v1")
        lines.append(
            f"Harvest: transcript:{session_id} · messages_sha256:{sha_token} · "
            f"codec:{codec} · surface:{harvest_surface}"
        )
    mission_line = (
        mission.strip() or "Mission: resume continuity house from this CHECKPOINT."
    )
    if not mission_line.startswith("Mission:"):
        mission_line = f"Mission: {mission_line}"
    lines.extend(["", mission_line, CANONICAL_RESUME_FOOTER])
    return "\n".join(lines)


class ContinuityCheckpointPostHandler(BaseHandler):
    """Post CHECKPOINT turn with after_turn guard and terminal result dict."""

    step_type = "continuity_checkpoint_post_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        from_agent = str(options.get("from_agent") or "continuity")
        surface = str(options.get("surface") or "cursor")
        execution_id = str(context.execution_id or "")

        outputs = getattr(context, "outputs", {}) or {}
        seal = step_output_json(outputs, "seal")
        resolve = step_output_json(outputs, "resolve")
        pre = step_output_json(outputs, "pre_consolidate")
        score = step_output_json(outputs, "score")
        tail = step_output_json(outputs, "tail_mechanical")
        # Seal is skipped when resolve refuses; an empty seal plus caller
        # residue used to post a hollow CHECKPOINT tip (Window: transcript_id=
        # · turns@cp=0). Never let residue promote a refused/skipped seal.
        if resolve.get("refused") and not seal.get("refused"):
            seal = {"refused": resolve["refused"]}
        elif not seal.get("refused") and not (
            seal.get("transcript_id") or seal.get("chat_url")
        ):
            seal = {
                "refused": {
                    "code": "checkpoint.seal_skipped",
                    "message": "seal produced no window (step skipped or hollow)",
                }
            }
        caller_residue = str(options.get("residue") or "").strip()

        if not pre:
            # Skipped pre_consolidate must not look dispatched (a:33299).
            pre = {
                "residue": (
                    "TYPE: CHECKPOINT · pipeline · seal refused or pre_consolidate skipped.\n"
                    f"Mission: record checkpoint for {thread}.\n"
                ),
                "mission": f"resume {thread}",
                "executor": "skipped",
                "card_patch_applied": False,
            }

        pre_source = pre.get("residue_source")
        mission = str(pre.get("mission") or "")
        residue_clamped = False

        tip, _ = await bus_get(
            "/turns/by-number",
            params={"thread": thread, "turn_number": "latest"},
        )
        after_turn = int(tip.get("turn_number") or 0) if isinstance(tip, dict) else 0
        prior_body = str(tip.get("body") or "") if isinstance(tip, dict) else ""

        if pre_source in ("model", "seed", "mechanical"):
            residue, residue_clamped = clamp_residue(str(pre.get("residue") or ""))
            residue_source = pre_source
        elif caller_residue:
            residue, residue_clamped = clamp_residue(caller_residue)
            residue_source = "caller"
        elif pre_source is None:
            carried = _carry_forward_residue(
                prior_body=prior_body, prior_turn=after_turn
            )
            if carried:
                residue, residue_clamped = clamp_residue(carried)
                residue_source = "carried_forward"
            else:
                residue, residue_clamped = clamp_residue(str(pre.get("residue") or ""))
                residue_source = pre_source or "mechanical"
        else:
            residue, residue_clamped = clamp_residue(str(pre.get("residue") or ""))
            residue_source = pre_source or "mechanical"

        checkpoint_channel = str(options.get("channel") or "continuity")
        body = _compose_body(
            residue=residue,
            seal=seal,
            mission=mission,
            surface=surface,
            channel=checkpoint_channel,
        )
        scoreboard_pin = tail.get("scoreboard_pin")
        # Charter projection_only sets a pin + fold_row_lines while folded=False.
        # Treating the pin as a live fold dumped the whole board into authored
        # residue (10479 lean post 413: authored 85k / 8k).
        live_tail_pin = bool(tail.get("folded"))
        if not scoreboard_pin and score.get("tip_sha"):
            scoreboard_pin = (
                f"Scoreboard: {score.get('tip_uri')} · sha256:{score['tip_sha']}"
            )
        if not scoreboard_pin:
            scoreboard_pin = _carry_forward_scoreboard_pin(prior_body)
        if scoreboard_pin:
            body = body.replace("## Anchor", f"## Anchor\n{scoreboard_pin}", 1)
            # G-rows come only from a live tail fold — not score synthesis or carry-forward.
            if live_tail_pin:
                for row_line in tail.get("fold_row_lines") or ():
                    body = f"{body.rstrip()}\n{row_line}\n"
        supersedes_tip = not bool(seal.get("refused"))

        slug = thread[:12]
        subject = (
            f"CHECKPOINT {slug} {execution_id[:8]}"
            if supersedes_tip
            else f"INFO — checkpoint {slug} {execution_id[:8]} seal refused"
        )
        send_resp, send_status = await bus_send(
            thread=thread,
            from_agent=from_agent,
            to_agent="web",
            subject=subject,
            body=body,
            after_turn=after_turn,
        )
        if send_status == 409:
            detail = send_resp.get("detail") or send_resp
            if isinstance(detail, dict):
                latest = int(detail.get("latest_turn_number") or after_turn)
                send_resp, send_status = await bus_send(
                    thread=thread,
                    from_agent=from_agent,
                    to_agent="web",
                    subject=subject,
                    body=body,
                    after_turn=latest,
                )

        if send_status >= 400:
            from continuity_tape.events import stargate_continuity_checkpoint_failed

            code = str(
                (send_resp.get("error") or {}).get("code")
                or send_resp.get("code")
                or f"http_{send_status}"
            )
            stargate_continuity_checkpoint_failed(
                execution_id=execution_id,
                thread=thread,
                surface=surface,
                from_agent=from_agent,
                stage="post",
                code=code,
            )
            payload = {"error": send_resp, "status_code": send_status}
            return StepOutput(raw=json.dumps(payload), json=payload)

        turn_raw = send_resp.get("turn_number") or send_resp.get("turn") or 0
        if isinstance(turn_raw, dict):
            turn_raw = turn_raw.get("turn_number") or turn_raw.get("number") or 0
        bus_turn = int(turn_raw or 0)
        stargate_continuity_checkpoint_posted(
            execution_id=execution_id,
            thread=thread,
            surface=surface,
            from_agent=from_agent,
            bus_turn=bus_turn,
        )

        session_id = seal.get("session_id") or ""
        result = {
            "execution_id": execution_id,
            "thread": thread,
            "surface": surface,
            "status": "posted",
            "bus_turn": bus_turn,
            "pre_consolidate": {
                "executor": pre.get("executor") or "skipped",
                "card_patch_applied": bool(pre.get("card_patch_applied")),
                "residue_source": residue_source,
                "residue_clamped": residue_clamped,
                "supersedes_tip": supersedes_tip,
            },
            "harvest_entity_id": f"transcript:{session_id}" if session_id else None,
            "seal": {
                "turn_count": seal.get("turn_count"),
                "already_closed": seal.get("already_closed"),
                "refused": seal.get("refused"),
            },
            "scoreboard": {
                "tip_sha": tail.get("scoreboard_sha256") or score.get("tip_sha"),
                "tip_uri": tail.get("scoreboard_uri") or score.get("tip_uri"),
                "skipped": not bool(tail.get("folded")),
                "fold": tail or score.get("fold"),
                "family": tail.get("family"),
                "card_written": tail.get("card_written"),
            },
            "tail_mechanical": tail,
        }
        return StepOutput(raw=json.dumps(result, default=str), json=result)
