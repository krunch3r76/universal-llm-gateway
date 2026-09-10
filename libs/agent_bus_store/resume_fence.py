"""Resume bundle assembly and manifest derivation for structural resume fence."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from deploy_identity.code_version import resolve_code_version

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect
from .house_pools import (
    continuity_card_uri,
    load_continuity_card,
    parse_pools,
)
from .resume_envelope import build_resume_envelope
from .resume_fence_store import (
    _armed_source,
    adoption_ambiguous_count,
    append_fence_event,
    find_open_fence,
    fold_fence,
    mint_fence_id,
    pour_terminal_release,
    read_set_from_journal,
)

_BUNDLE_VERSION = "resume-bundle-v1"
_PROJECTION_URI = "cortex://notes/system/threads/{thread}-transcript-projection.md"
_OPPORTUNITIES_URI = "cortex://notes/system/threads/{thread}-opportunities.md"
_OPEN_LINE_RE = re.compile(r"(?m)^In one line:\s*(.+)$")
_BUS_THREAD_RE = re.compile(r"agent-bus:(\d{3,6})(?:#(\d+))?")
_CORTEX_URI_RE = re.compile(r"cortex://[^\s)\]>`]+")
_ENTITY_RE = re.compile(
    r"\b(?:todo|decision|document|transcript):[^\s)\]>]+",
    re.IGNORECASE,
)
_GIT_SHA_RE = re.compile(r"\b(?:git:)?([0-9a-f]{7,40})\b", re.IGNORECASE)
_TRANSCRIPT_ID_RE = re.compile(
    r"transcript_id\s*=\s*([0-9a-f-]{8,})",
    re.IGNORECASE,
)
_SECTION_CHILD = "### Child lanes"
_SECTION_CITED = "### Cited lanes"
RESUME_FENCE_TAPE_BUDGET = int(os.environ.get("RESUME_FENCE_TAPE_BUDGET", "24000"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tip_checkpoint(thread_id: str) -> dict[str, Any] | None:
    cps = list_checkpoint_turns(thread_id=thread_id)
    if not cps:
        return None
    tip = cps[-1]
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, turn_number, subject, body, supersedes_turn, created_at
            FROM turns WHERE thread = ? AND turn_number = ?
            """,
            (thread_id, tip.turn_number),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def _section_lines(body: str, header: str) -> list[str]:
    if header not in body:
        return []
    chunk = body.split(header, 1)[1]
    for marker in (
        "### Artifact anchors",
        "### Entity / assertion rows",
        "### In-flight producers",
        "## ",
    ):
        if marker in chunk:
            chunk = chunk.split(marker, 1)[0]
            break
    return [ln.strip() for ln in chunk.splitlines() if ln.strip() and ln.strip() != "_none_"]


def _extract_resume_open(card_text: str | None) -> str | None:
    if not card_text or "## Resume open" not in card_text:
        return None
    chunk = card_text.split("## Resume open", 1)[1]
    for marker in ("## Opportunities", "## Pools", "## Sidecars", "## "):
        if marker in chunk:
            chunk = chunk.split(marker, 1)[0]
            break
    text = chunk.strip()
    return text or None


def _thread_ids_from_section(lines: list[str]) -> set[str]:
    found: set[str] = set()
    for line in lines:
        for match in _BUS_THREAD_RE.finditer(line):
            found.add(match.group(1))
    return found


def _sidecar_uris(card: str) -> list[str]:
    if "## Sidecars" not in card:
        return []
    chunk = card.split("## Sidecars", 1)[1].split("## ", 1)[0]
    return sorted(set(_CORTEX_URI_RE.findall(chunk)))


def _derive_read_set(
    *,
    thread_id: str,
    tip_body: str,
    tip_turn: int,
    supersedes_turn: int | None,
    card_text: str | None,
    pool: str | None,
) -> dict[str, Any]:
    card_uri = continuity_card_uri(thread_id)
    projection_uri = _PROJECTION_URI.format(thread=thread_id)
    opportunities_uri = _OPPORTUNITIES_URI.format(thread=thread_id)

    child_threads = _thread_ids_from_section(
        _section_lines(tip_body, _SECTION_CHILD)
    )
    cited_threads = _thread_ids_from_section(
        _section_lines(tip_body, _SECTION_CITED)
    )
    citable_threads = {thread_id, *child_threads, *cited_threads}

    cortex_uris = sorted(
        set(_CORTEX_URI_RE.findall(tip_body))
        | {card_uri, projection_uri, opportunities_uri}
        | set(_sidecar_uris(card_text or ""))
    )
    entities = sorted(set(_ENTITY_RE.findall(tip_body)))
    entity_doc = f"document:{thread_id}-continuity"
    if entity_doc not in entities:
        entities.append(entity_doc)

    bus_turns = [f"{thread_id}#{tip_turn}"]
    if supersedes_turn:
        with connect() as conn:
            row = conn.execute(
                "SELECT turn_number FROM turns WHERE id = ?",
                (supersedes_turn,),
            ).fetchone()
        if row:
            bus_turns.append(f"{thread_id}#{row['turn_number']}")

    git_shas = sorted({m.group(1).lower() for m in _GIT_SHA_RE.finditer(tip_body)})
    transcripts = sorted(set(_TRANSCRIPT_ID_RE.findall(tip_body)))

    readable_mcp = [
        {
            "tool": "continuity",
            "ops": ["resume", "status", "resume_release", "tape_read"],
            "thread": thread_id,
        },
        {
            "tool": "agent_bus_read",
            "ops": ["get", "thread_get"],
            "thread": thread_id,
        },
        {
            "tool": "fs",
            "ops": ["read", "md_read", "md_list"],
            "paths": cortex_uris,
        },
        {
            "tool": "cortex",
            "ops": ["entity_get"],
            "ids": entities,
        },
        {
            "tool": "agent_bus",
            "ops": ["send"],
            "thread": thread_id,
            "requires": "fence_id",
        },
        {
            "tool": "retrieve",
            "id_prefix": "rs_",
        },
    ]

    pools_row: str | None = None
    if card_text and pool:
        try:
            pools_row = _format_pool_row(parse_pools(card_text)[pool])
        except Exception:
            pools_row = None

    return {
        "readable": {
            "bus_threads": [thread_id],
            "bus_turns": bus_turns,
            "cortex_uris": cortex_uris,
            "entities": entities,
            "fs_paths": [],
            "shell": False,
            "mcp_allow": readable_mcp,
        },
        "citable": {
            "bus_threads": sorted(citable_threads),
            "git": git_shas,
            "transcripts": transcripts,
        },
        "pools_row": pools_row,
    }


def _format_pool_row(row: Any) -> str:
    return (
        f"| {row.pool} | {row.executor} | {row.status} | "
        f"{' · '.join(row.must_load)} | {' · '.join(row.must_read)} | "
        f"{row.closeout} | {row.forbidden} |"
    )


def _load_resume_context(
    thread_id: str,
    *,
    pool: str | None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    tip = _tip_checkpoint(thread_id)
    if tip is None:
        return None, None, None
    card_text = load_continuity_card(thread_id)
    read_set = _derive_read_set(
        thread_id=thread_id,
        tip_body=str(tip["body"]),
        tip_turn=int(tip["turn_number"]),
        supersedes_turn=tip.get("supersedes_turn"),
        card_text=card_text,
        pool=pool,
    )
    return tip, card_text, read_set


def arm_resume_fence(
    thread_id: str,
    *,
    transcript_id: str | None = None,
    source: str = "hook_prompt",
    pool: str | None = None,
) -> dict[str, Any]:
    """Arm a fence with ``read_set`` only — no tape render (FIX-8)."""
    tip, _card_text, read_set = _load_resume_context(thread_id, pool=pool)
    if tip is None or read_set is None:
        return {"error": "no_tip_checkpoint", "reason": "resume_fence.no_tip_checkpoint"}

    pools_row = read_set.pop("pools_row")
    fence_id = find_open_fence(root_thread=thread_id, transcript_id=transcript_id)
    opened_at = datetime.now(UTC).isoformat()
    state = "armed"

    if fence_id is None:
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
            },
        )
    else:
        folded = fold_fence(fence_id)
        if folded is not None:
            state = folded.state
            opened_at = folded.last_event_at or opened_at
            cached = read_set_from_journal(fence_id)
            if cached is not None:
                read_set = cached

    return {
        "fence": {
            "fence_id": fence_id,
            "root_thread": thread_id,
            "transcript_id": transcript_id,
            "state": state,
            "opened_at": opened_at,
        },
        "fence_carriage": {
            "fence_id": fence_id,
            "transcript_id": transcript_id,
            "durable_send_requires_fence_id": state in {"armed", "poured"},
            "first_hop": (
                f"continuity(op=resume, thread={thread_id}"
                f"{f', transcript_id={transcript_id}' if transcript_id else ''})"
            ),
        },
        "read_set": read_set,
        "pools_row": pools_row,
    }


def assemble_resume_fence(
    thread_id: str,
    *,
    transcript_id: str | None = None,
    source: str = "mcp",
    pool: str | None = None,
) -> dict[str, Any]:
    """Pour ResumeBundle v1 onto an armed or new fence."""
    tip, card_text, read_set = _load_resume_context(thread_id, pool=pool)
    if tip is None or read_set is None:
        return {"error": "no_tip_checkpoint", "reason": "resume_fence.no_tip_checkpoint"}

    card_uri = continuity_card_uri(thread_id)
    card_sha = _sha256_text(card_text) if card_text else None

    envelope = build_resume_envelope(
        thread_id,
        tape_budget_bytes=RESUME_FENCE_TAPE_BUDGET,
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
            payload={"source": source, "transcript_id": transcript_id, "read_set": read_set},
        )
    else:
        fence_id = mint_fence_id()
        append_fence_event(
            fence_id=fence_id,
            root_thread=thread_id,
            transcript_id=transcript_id,
            event="armed",
            payload={"source": source, "transcript_id": transcript_id, "read_set": read_set},
        )

    projection_uri = _PROJECTION_URI.format(thread=thread_id)
    open_line_match = _OPEN_LINE_RE.search(card_text or "")
    open_line = open_line_match.group(1).strip() if open_line_match else None
    verbal = envelope.get("tape_verbal") or []
    tape_bytes = len(json.dumps(verbal, ensure_ascii=False).encode("utf-8"))

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
        "mission": {
            "highlight": envelope.get("checkpoint_highlight"),
            "open_line": open_line,
            "summary_row": envelope.get("consolidate_summary_row"),
            "resume_open": _extract_resume_open(card_text),
            "pools_row": pools_row,
            "fence_id": fence_id,
        },
        "fence_carriage": {
            "fence_id": fence_id,
            "transcript_id": transcript_id,
            "durable_send_requires_fence_id": False,
            "first_hop": (
                f"continuity(op=resume, thread={thread_id}"
                f"{f', transcript_id={transcript_id}' if transcript_id else ''})"
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
            "bytes": tape_bytes,
            "scope": envelope.get("scope", "last_session"),
            "read_via": {
                "tool": "continuity",
                "op": "tape_read",
                "thread": thread_id,
                "scope": "last_session",
            },
        },
        "card": {
            "uri": card_uri,
            "sha256": card_sha,
            "body": card_text or "",
        },
        "projection": {
            "uri": projection_uri,
            "sha256": None,
            "open_line": open_line,
        },
        "opportunities": {
            "uri": _OPPORTUNITIES_URI.format(thread=thread_id),
            "sha256": None,
        },
        "pools_row": pools_row,
        "read_set": read_set,
        "stance": "Use the ulg-for-llms skill.",
        "provenance": {
            "built_at": datetime.now(UTC).isoformat(),
            "sources": [
                {"uri": card_uri, "sha256": card_sha},
                {"uri": f"agent-bus:{thread_id}#{tip['turn_number']}", "sha256": None},
            ],
        },
    }

    bundle_bytes = len(json.dumps(bundle, ensure_ascii=False))
    readable = bundle["read_set"]["readable"]
    poured_payload: dict[str, Any] = {
        "bundle_bytes": bundle_bytes,
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

    append_fence_event(
        fence_id=fence_id,
        root_thread=thread_id,
        transcript_id=transcript_id,
        event="poured",
        payload=poured_payload,
    )
    poured_terminal = pour_terminal_release(fence_id=fence_id)
    if poured_terminal is not None:
        bundle["fence"]["state"] = poured_terminal.state
    return bundle


__all__ = ["RESUME_FENCE_TAPE_BUDGET", "arm_resume_fence", "assemble_resume_fence"]
