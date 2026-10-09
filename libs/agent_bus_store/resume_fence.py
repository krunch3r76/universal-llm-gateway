"""Resume bundle assembly and manifest derivation for structural resume fence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root

from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL
from .continuity_card_scratchboards import extract_scratchboard_uris
from .db.connection import connect, write_connect
from .house_pools import (
    ContinuityCard,
    _continuity_card_candidate_relpaths,
    load_continuity_card,
    parse_pools,
)
from .resume_fence_emit import fence_writes_then_emit
from .resume_fence_mission import (
    build_mission_block,
    house_unread_turns,
    mission_marker_preview,
)
from .resume_fence_store import (
    append_fence_event,
    find_open_fence,
    fold_fence,
    mint_fence_id,
    pour_terminal_release,
    read_set_from_journal,
)
from .tape_degrade import TAPE_BUDGET_BYTES_DEFAULT

_BUNDLE_VERSION = "resume-bundle-v2"
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


def _resume_first_hop(
    thread_id: str,
    *,
    transcript_id: str | None,
    surface: str | None,
) -> str:
    hop = f"continuity(op=resume, thread={thread_id}"
    if transcript_id:
        hop += f", transcript_id={transcript_id}"
    if surface == "cursor":
        hop += ", surface=cursor"
    return hop + ")"


_TRANSCRIPT_ID_RE = re.compile(
    r"transcript_id\s*=\s*([0-9a-f-]{8,})",
    re.IGNORECASE,
)
_SECTION_CHILD = "### Child lanes"
_SECTION_CITED = "### Cited lanes"


# Matches the other tape doors (TAPE_BUDGET_BYTES_DEFAULT, GET /threads/{id}/tape).
# RESUME_FENCE_TAPE_BUDGET env overrides only at this resume door.
def _resume_fence_tape_budget() -> tuple[int, str]:
    env_val = os.environ.get("RESUME_FENCE_TAPE_BUDGET")
    if env_val is not None:
        return int(env_val), "env"
    return TAPE_BUDGET_BYTES_DEFAULT, "default"


RESUME_FENCE_TAPE_BUDGET, RESUME_FENCE_BUDGET_SOURCE = _resume_fence_tape_budget()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_resume_bundle(bundle: dict[str, Any]) -> bytes:
    return json.dumps(
        bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def resume_bundle_sha256(bundle: dict[str, Any]) -> str:
    return hashlib.sha256(encode_resume_bundle(bundle)).hexdigest()


def _thread_slug(thread_id: str) -> str | None:
    """Resolve thread slug for handoff rename steps (a:33192)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT slug FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    if row and row["slug"]:
        return str(row["slug"])
    return None


def _tip_checkpoint(thread_id: str, conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Latest CHECKPOINT turn on *conn*, which must be the open write transaction.

    A reader ``connect()`` can return the previous tip while a checkpoint
    insert still holds the writer ticket. Selecting here, after ``BEGIN
    IMMEDIATE``, sees the tip that committed before this ticket was granted.
    """
    row = conn.execute(
        f"""
        SELECT id, turn_number, subject, body, supersedes_turn, created_at
        FROM turns
        WHERE thread = ? AND {CHECKPOINT_SUBJECT_SQL}
        ORDER BY turn_number DESC
        LIMIT 1
        """,
        (thread_id,),
    ).fetchone()
    if row is None:
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
    return [
        ln.strip() for ln in chunk.splitlines() if ln.strip() and ln.strip() != "_none_"
    ]


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
    card: ContinuityCard,
    pool: str | None,
) -> dict[str, Any]:
    card_text = card.text if card.status == "found" else None
    projection_uri = _PROJECTION_URI.format(thread=thread_id)
    opportunities_uri = _OPPORTUNITIES_URI.format(thread=thread_id)
    located: set[str] = {projection_uri, opportunities_uri}
    if card.status == "found" and card.uri:
        located.add(card.uri)

    child_threads = _thread_ids_from_section(_section_lines(tip_body, _SECTION_CHILD))
    cited_threads = _thread_ids_from_section(_section_lines(tip_body, _SECTION_CITED))
    citable_threads = {thread_id, *child_threads, *cited_threads}

    cortex_uris = sorted(
        set(_CORTEX_URI_RE.findall(tip_body))
        | located
        | set(_sidecar_uris(card_text or ""))
        | set(extract_scratchboard_uris(card_text or ""))
    )
    entities = sorted(set(_ENTITY_RE.findall(tip_body)))
    entity_doc = f"document:{thread_id}-continuity"
    if entity_doc not in entities:
        entities.append(entity_doc)

    bus_turns = [f"{thread_id}#{tip_turn}"]
    for item in house_unread_turns(thread_id):
        ref = f"{thread_id}#{item['turn']}"
        if ref not in bus_turns:
            bus_turns.append(ref)
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
            "ops": ["get", "thread_get", "fetch_unread"],
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


def _otherwise_derived_uris(tip_body: str, card: ContinuityCard) -> set[str]:
    """URIs a fresh derivation would keep besides the resolved card itself."""
    card_text = card.text if card.status == "found" else ""
    return (
        set(_CORTEX_URI_RE.findall(tip_body))
        | set(_sidecar_uris(card_text or ""))
        | set(extract_scratchboard_uris(card_text or ""))
    )


def _card_uri_on_disk(uri: str) -> bool:
    if not isinstance(uri, str) or not uri.startswith("cortex://"):
        return False
    return (cortex_files_root() / uri.removeprefix("cortex://")).is_file()


def _reresolve_card_uris(
    read_set: dict[str, Any],
    *,
    thread_id: str,
    card: ContinuityCard,
    tip_body: str,
) -> dict[str, Any]:
    """Replace a stale guessed card URI and keep derived archive URIs.

    A cached ``{id}-continuity.md`` that is no longer the resolved card, and
    is not cited by the tip or listed under ``## Sidecars``, is dropped.
    The same name stays when the current card lists it (dual-file archive).
    A ladder URI whose file is gone is dropped either way.
    """
    ladder = {
        f"cortex://{rel}" for rel in _continuity_card_candidate_relpaths(thread_id)
    }
    resolved = card.uri if card.status == "found" else None
    derived = _otherwise_derived_uris(tip_body, card)

    def _keep(item: Any) -> bool:
        if item not in ladder:
            return True
        if not _card_uri_on_disk(item):
            return False
        if resolved and item == resolved:
            return True
        return item in derived

    def _rewrite(values: list[Any]) -> list[Any]:
        kept = [item for item in values if _keep(item)]
        extras = {uri for uri in derived if uri in ladder and _card_uri_on_disk(uri)}
        if resolved and _card_uri_on_disk(resolved):
            extras.add(resolved)
        for uri in extras:
            if uri not in kept:
                kept.append(uri)
        return sorted(kept)

    readable = read_set.get("readable")
    if not isinstance(readable, dict):
        return read_set
    uris = readable.get("cortex_uris")
    if isinstance(uris, list):
        readable["cortex_uris"] = _rewrite(uris)
    allow = readable.get("mcp_allow")
    if isinstance(allow, list):
        for row in allow:
            if (
                isinstance(row, dict)
                and row.get("tool") == "fs"
                and isinstance(row.get("paths"), list)
            ):
                row["paths"] = _rewrite(row["paths"])
    return read_set


def _load_resume_context(
    thread_id: str,
    *,
    pool: str | None,
    conn: sqlite3.Connection,
) -> tuple[dict[str, Any] | None, ContinuityCard | None, dict[str, Any] | None]:
    tip = _tip_checkpoint(thread_id, conn)
    if tip is None:
        return None, None, None
    card = load_continuity_card(thread_id)
    read_set = _derive_read_set(
        thread_id=thread_id,
        tip_body=str(tip["body"]),
        tip_turn=int(tip["turn_number"]),
        supersedes_turn=tip.get("supersedes_turn"),
        card=card,
        pool=pool,
    )
    return tip, card, read_set


def arm_resume_fence(
    thread_id: str,
    *,
    transcript_id: str | None = None,
    source: str = "hook_prompt",
    pool: str | None = None,
    surface: str | None = None,
) -> dict[str, Any]:
    """Arm a resume fence on the write transaction that also reads the tip.

    The latest checkpoint is selected on that connection after the writer
    ticket is granted and ``BEGIN IMMEDIATE`` has run, and the fence row is
    inserted before commit. An in-flight checkpoint insert therefore commits
    before this read. Returns the arm payload, or an error dict when the
    thread has no checkpoint. The armed signal fires after commit. Tape is
    not rendered on this path.
    """
    with fence_writes_then_emit():
        with write_connect() as conn:
            return _arm_resume_fence_in_txn(
                conn,
                thread_id,
                transcript_id=transcript_id,
                source=source,
                pool=pool,
                surface=surface,
            )


def _arm_resume_fence_in_txn(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    transcript_id: str | None,
    source: str,
    pool: str | None,
    surface: str | None,
) -> dict[str, Any]:
    tip, card, read_set = _load_resume_context(thread_id, pool=pool, conn=conn)
    if tip is None or card is None or read_set is None:
        return {
            "error": "no_tip_checkpoint",
            "reason": "resume_fence.no_tip_checkpoint",
        }

    pools_row = read_set.pop("pools_row")
    fence_id = find_open_fence(root_thread=thread_id, transcript_id=transcript_id)
    opened_at = datetime.now(UTC).isoformat()
    state = "armed"

    mission_preview: dict[str, Any] | None = None

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
                "surface": surface,
            },
        )
    else:
        folded = fold_fence(fence_id)
        if folded is not None:
            state = folded.state
            opened_at = folded.last_event_at or opened_at
            cached = read_set_from_journal(fence_id)
            if cached is not None:
                read_set = _reresolve_card_uris(
                    cached,
                    thread_id=thread_id,
                    card=card,
                    tip_body=str(tip["body"]),
                )

    card_text = card.text if card.status == "found" else None
    open_line_match = _OPEN_LINE_RE.search(card_text or "")
    open_line = open_line_match.group(1).strip() if open_line_match else None
    mission_preview = mission_marker_preview(
        build_mission_block(
            thread_id=thread_id,
            tip_body=str(tip["body"]),
            tip_turn=int(tip["turn_number"]),
            supersedes_turn=tip.get("supersedes_turn"),
            card_text=card_text,
            envelope={},
            pools_row=pools_row,
            open_line=open_line,
            fence_id=fence_id,
            thread_slug=_thread_slug(thread_id),
        )
    )

    payload: dict[str, Any] = {
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
            "first_hop": _resume_first_hop(
                thread_id,
                transcript_id=transcript_id,
                surface=surface,
            ),
        },
        "read_set": read_set,
        "pools_row": pools_row,
    }
    if mission_preview is not None:
        payload["mission_preview"] = mission_preview
    return payload


def assemble_resume_fence(
    thread_id: str,
    *,
    transcript_id: str | None = None,
    source: str = "mcp",
    pool: str | None = None,
    surface: str | None = None,
    pre_pour_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pour a resume bundle whose tip is read inside the fence write.

    After the writer ticket is granted, ``BEGIN IMMEDIATE`` runs, the latest
    checkpoint tip is selected on that connection, and the fence row is
    inserted before commit. A checkpoint insert already in ``write_connect``
    commits first, so the bundle's tip is that checkpoint. Returns the bundle,
    or an error dict when the thread has no checkpoint or the tape render
    fails. The pour-terminal release is the next write, after this commit.
    """
    with fence_writes_then_emit():
        with write_connect() as conn:
            from .resume_fence_pour import assemble_resume_fence_in_txn

            bundle = assemble_resume_fence_in_txn(
                conn,
                thread_id,
                transcript_id=transcript_id,
                source=source,
                pool=pool,
                surface=surface,
                pre_pour_harvest=pre_pour_harvest,
            )
        if not bundle.get("error"):
            pour_terminal_release(fence_id=bundle["fence"]["fence_id"])
        return bundle


__all__ = [
    "RESUME_FENCE_TAPE_BUDGET",
    "arm_resume_fence",
    "assemble_resume_fence",
    "encode_resume_bundle",
    "resume_bundle_sha256",
]
