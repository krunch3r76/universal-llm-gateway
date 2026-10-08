"""Build a resume bundle inside the fence-row write transaction.

``assemble_resume_fence`` holds the writer ticket, selects the latest
checkpoint tip on that connection, and calls here before commit. The fence
row this module inserts records that same tip. Callers keep the pour-terminal
release outside the transaction so a reader can see the poured row.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from deploy_identity.code_version import resolve_code_version

from .resume_envelope import build_resume_envelope
from .resume_fence_mission import build_mission_block, cap_standing_rule_claims
from .resume_fence_store import (
    _armed_source,
    adoption_ambiguous_count,
    append_fence_event,
    find_open_fence,
    fold_fence,
    mint_fence_id,
)


def assemble_resume_fence_in_txn(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    transcript_id: str | None,
    source: str,
    pool: str | None,
    surface: str | None,
    pre_pour_harvest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the pour bundle from the tip selected on *conn* and insert it.

    *conn* is the open fence write, past ``BEGIN IMMEDIATE``. The returned
    bundle's checkpoint is that tip. Fence inserts join the same transaction.
    Returns an error dict when the thread has no checkpoint or tape render
    fails, without inserting a poured row.
    """
    from .resume_fence import (
        _BUNDLE_VERSION,
        _OPEN_LINE_RE,
        _OPPORTUNITIES_URI,
        _PROJECTION_URI,
        RESUME_FENCE_BUDGET_SOURCE,
        RESUME_FENCE_TAPE_BUDGET,
        _load_resume_context,
        _resume_first_hop,
        _thread_slug,
        resume_bundle_sha256,
    )

    tip, card, read_set = _load_resume_context(thread_id, pool=pool, conn=conn)
    if tip is None or card is None or read_set is None:
        return {
            "error": "no_tip_checkpoint",
            "reason": "resume_fence.no_tip_checkpoint",
        }

    card_text = card.text if card.status == "found" else None

    envelope = build_resume_envelope(
        thread_id,
        tape_budget_bytes=RESUME_FENCE_TAPE_BUDGET,
        budget_source=RESUME_FENCE_BUDGET_SOURCE,
        tip_turn=int(tip["turn_number"]),
        tip_body=str(tip["body"] or ""),
    )
    if envelope.get("error"):
        return envelope

    pools_row = read_set.pop("pools_row")
    ambiguous = adoption_ambiguous_count(thread_id) if transcript_id is None else 0

    fence_id = find_open_fence(root_thread=thread_id, transcript_id=transcript_id)
    adopted_from: str | None = None
    if fence_id is not None:
        folded = fold_fence(fence_id)
        if folded is not None and folded.state == "armed":
            adopted_from = _armed_source(fence_id)
    elif ambiguous > 1:
        fence_id = mint_fence_id()
        append_fence_event(
            fence_id=fence_id,
            root_thread=thread_id,
            transcript_id=transcript_id,
            event="armed",
            payload={
                "source": source,
                "transcript_id": transcript_id,
                "read_set": read_set,
                "surface": surface,
            },
        )
    else:
        fence_id = mint_fence_id()
        append_fence_event(
            fence_id=fence_id,
            root_thread=thread_id,
            transcript_id=transcript_id,
            event="armed",
            payload={
                "source": source,
                "transcript_id": transcript_id,
                "read_set": read_set,
                "surface": surface,
            },
        )

    projection_uri = _PROJECTION_URI.format(thread=thread_id)
    open_line_match = _OPEN_LINE_RE.search(card_text or "")
    open_line = open_line_match.group(1).strip() if open_line_match else None
    verbal = envelope.get("tape_verbal") or []
    tape_bytes = len(json.dumps(verbal, ensure_ascii=False).encode("utf-8"))
    mission = build_mission_block(
        thread_id=thread_id,
        tip_body=str(tip["body"]),
        tip_turn=int(tip["turn_number"]),
        supersedes_turn=tip.get("supersedes_turn"),
        card_text=card_text,
        envelope=envelope,
        pools_row=pools_row,
        open_line=open_line,
        fence_id=fence_id,
        thread_slug=_thread_slug(thread_id),
    )
    mission["standing_rules"] = cap_standing_rule_claims(
        mission.get("standing_rules") or []
    )

    bundle: dict[str, Any] = {
        "bundle_version": _BUNDLE_VERSION,
        "fence": {
            "fence_id": fence_id,
            "root_thread": thread_id,
            "transcript_id": transcript_id,
            "state": "poured",
            "opened_at": datetime.now(UTC).isoformat(),
            "head_sha": resolve_code_version(),
        },
        "mission": mission,
        "skills_to_use": mission.get("skills_to_use") or [],
        "fence_carriage": {
            "fence_id": fence_id,
            "transcript_id": transcript_id,
            "durable_send_requires_fence_id": False,
            "first_hop": _resume_first_hop(
                thread_id,
                transcript_id=transcript_id,
                surface=surface,
            ),
        },
        "tip_checkpoint": {
            "turn_number": tip["turn_number"],
            "turn_id": tip["id"],
            "subject": tip["subject"],
            "body": tip["body"],
            "supersedes_turn": tip.get("supersedes_turn"),
            "created_at": tip.get("created_at"),
        },
        "resume_envelope": envelope,
        "tape": {
            "message_count": envelope.get("message_count", len(verbal)),
            "truncated": envelope.get("tape_truncated", False),
            "degraded": envelope.get("tape_degraded"),
            "bytes": tape_bytes,
            "scope": envelope.get("scope", "last_session"),
            "read_via": {
                "tool": "continuity",
                "op": "tape_read",
                "thread": thread_id,
                "scope": "last_session",
            },
        },
        "card": (
            {
                "uri": card.uri,
                "sha256": card.sha256,
                "read_via": {"tool": "fs", "op": "read", "path": card.uri},
            }
            if card.status == "found"
            else {"status": "missing", "tried": list(card.tried)}
        ),
        "projection": {
            "uri": projection_uri,
            "sha256": None,
            "open_line": open_line,
        },
        "opportunities": {
            "uri": _OPPORTUNITIES_URI.format(thread=thread_id),
            "sha256": None,
        },
        "read_set": read_set,
        "stance": "Use the ulg-for-llms skill.",
        "provenance": {
            "built_at": datetime.now(UTC).isoformat(),
            "sources": (
                [{"uri": card.uri, "sha256": card.sha256}]
                if card.status == "found"
                else []
            )
            + [
                {"uri": f"agent-bus:{thread_id}#{tip['turn_number']}", "sha256": None},
            ],
        },
    }
    if pre_pour_harvest is not None:
        bundle["pre_pour_harvest"] = pre_pour_harvest

    bundle_bytes = len(json.dumps(bundle, ensure_ascii=False))
    mission_bytes = len(json.dumps(mission, ensure_ascii=False))
    readable = bundle["read_set"]["readable"]
    skills_to_use = bundle["skills_to_use"]
    # card_inlined is True iff the poured bundle carries card-derived
    # directive content inline: at least one `## Rules` row in
    # mission.standing_rules or one `## Skills` slug in
    # mission.skills_to_use. Bundle v2 never inlines the full card body
    # (bundle.card is uri + sha256 + read_via). False means the bundle
    # has no card directives and the reader fs-reads bundle.card.uri
    # for them.
    standing_rules = mission.get("standing_rules") or []
    poured_payload: dict[str, Any] = {
        "bundle_bytes": bundle_bytes,
        "mission_bytes": mission_bytes,
        "card_inlined": bool(standing_rules) or bool(skills_to_use),
        "bundle_version": _BUNDLE_VERSION,
        "skills_to_use_count": len(skills_to_use),
        "readable_counts": {
            "bus_threads": len(readable["bus_threads"]),
            "cortex_uris": len(readable["cortex_uris"]),
            "entities": len(readable["entities"]),
        },
        "seal_status": envelope.get("seal_status", ""),
        "read_set": read_set,
    }
    if adopted_from:
        poured_payload["adopted_from"] = adopted_from
    if ambiguous > 1:
        poured_payload["adoption_ambiguous"] = ambiguous
    if pre_pour_harvest is not None:
        poured_payload["pre_pour_harvest"] = pre_pour_harvest

    poured_payload["bundle_sha256"] = resume_bundle_sha256(bundle)
    append_fence_event(
        fence_id=fence_id,
        root_thread=thread_id,
        transcript_id=transcript_id,
        event="poured",
        payload=poured_payload,
    )
    return bundle
