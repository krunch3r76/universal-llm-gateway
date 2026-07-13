"""Thread routes - CRUD for conversation threads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..auth import require_token
from ..db import (
    PendingShellContention,
    SlugExists,
    ThreadHasReadTurns,
    admit_dispatch,
    claim_and_post_turn,
    close_thread,
    consume_triage_confirm_token,
    create_thread,
    create_thread_with_turn,
    create_turn,
    delete_thread,
    execute_triage_close,
    execute_triage_mark_read,
    get_dispatch_link_by_execution_id,
    get_thread,
    get_thread_summary,
    get_thread_turns_asc,
    issue_triage_confirm_token,
    list_threads_v2,
    list_triage_candidates,
    normalize_thread_id,
    rename_thread,
    terminate_dispatch,
    update_thread,
)
from ..db.turns import UnreadTurnsExist
from ..turns_models import (
    TRIAGE_THREAD_CAP,
    DispatchAdmit,
    DispatchClaimAndPost,
    DispatchLinkByExecution,
    DispatchLinkSummary,
    DispatchTerminate,
    ThreadClose,
    ThreadCreate,
    ThreadDetail,
    ThreadListResponse,
    ThreadRename,
    ThreadStatus,
    ThreadSummaryResponse,
    ThreadTriageCandidate,
    ThreadTriageDryRun,
    ThreadTriageExecuted,
    ThreadTriageRequest,
    ThreadUpdate,
    ThreadWithTurnCreate,
    ThreadWithTurnCreated,
    TurnCreated,
    TurnSendCreate,
    TurnSendCreated,
    parse_older_than,
    post_continuation_misuse_error,
    sidecar_content_limit_error,
    sidecar_write_failed_envelope,
    triage_floor_error,
    turn_body_limit_error,
)

router = APIRouter(dependencies=[Depends(require_token)])


def _thread_detail(row: dict[str, Any]) -> ThreadDetail:
    """Convert a thread aggregate row to the typed API response model."""
    raw_links = row.get("dispatch_links") or []
    links = [
        DispatchLinkSummary(
            execution_id=lnk["execution_id"],
            pipeline_id=lnk["pipeline_id"],
            linked_at=datetime.fromisoformat(lnk["linked_at"]),
            terminal_status=lnk.get("terminal_status"),
            delivery_at=(
                datetime.fromisoformat(lnk["delivery_at"])
                if lnk.get("delivery_at")
                else None
            ),
        )
        for lnk in raw_links
    ]
    return ThreadDetail(
        id=row["id"],
        slug=row["slug"],
        status=row["status"],
        summary=row["summary"],
        turn_count=row["turn_count"],
        unread_count=row["unread_count"],
        last_subject=row["last_subject"],
        last_turn_from=row["last_turn_from"],
        last_turn_to=row["last_turn_to"],
        tags=row.get("tags", []) or [],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        bus_lifecycle_state=row.get("bus_lifecycle_state"),
        dispatch_links=links,
    )


@router.get(
    "/threads",
    response_model=ThreadListResponse,
)
async def list_threads_route(
    thread_status: ThreadStatus | None = Query(None, alias="status"),
    tags: list[str] | None = Query(None),
    lifecycle_state: str | None = Query(None),
    has_unread: bool | None = Query(
        None,
        description=(
            "When true, only return threads with at least one unread turn. "
            "When false, only return threads with zero unread turns. Omit "
            "for no unread filtering (default)."
        ),
    ),
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Cap the result count after ordering by most recent update. "
            "Boot consumers pair this with `has_unread=true&limit=10` to "
            "deliver only the inbound attention list without paginating "
            "the full active-thread set."
        ),
    ),
    query: str | None = Query(
        None,
        description=(
            "Case-insensitive substring match over slug, summary, and "
            "last_subject. Clamped to 200 characters server-side."
        ),
    ),
) -> ThreadListResponse:
    """List threads with optional status + AND-tag + lifecycle_state filtering.

    `tags`: repeat the param to filter on multiple tags (AND semantics).
    Example: `GET /threads?tags=project:X&tags=type:bug`.

    `lifecycle_state`: filter by exact lifecycle state value.
    Example: `GET /threads?lifecycle_state=pending`.

    `has_unread` + `limit`: compact attention projection.
    Example: `GET /threads?status=active&has_unread=true&limit=10`.

    `query`: free-text lookup composed with other filters.
    Example: `GET /threads?query=wave-b&status=active`.
    """
    rows = list_threads_v2(
        status=thread_status,
        tags=tags,
        lifecycle_state=lifecycle_state,
        has_unread=has_unread,
        limit=limit,
        query=query,
    )
    return ThreadListResponse(threads=[_thread_detail(r) for r in rows])


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
)
async def get_thread_route(thread_id: str) -> ThreadDetail:
    """Fetch one thread by id after normalizing numeric aliases first."""
    thread_id = normalize_thread_id(thread_id)
    row = get_thread(thread_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.get(
    "/dispatch-links/{execution_id}",
    response_model=DispatchLinkByExecution,
)
async def get_dispatch_link_route(execution_id: str) -> DispatchLinkByExecution:
    """Resolve execution_id to its durable dispatch link row."""
    row = get_dispatch_link_by_execution_id(execution_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dispatch link for execution_id {execution_id!r} not found",
        )
    terminal_at = row.get("terminal_at")
    return DispatchLinkByExecution(
        thread_id=row["thread_id"],
        pipeline_id=row["pipeline_id"],
        terminal_status=row.get("terminal_status"),
        terminal_at=(
            datetime.fromisoformat(terminal_at) if terminal_at is not None else None
        ),
    )


@router.get(
    "/threads/{thread_id}/summary",
    response_model=ThreadSummaryResponse,
)
async def get_thread_summary_route(
    thread_id: str, recent: int = Query(3)
) -> ThreadSummaryResponse:
    """Return thread summary plus a bounded list of most recent subjects."""
    thread_id = normalize_thread_id(thread_id)
    row = get_thread_summary(thread_id, recent=recent)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return ThreadSummaryResponse(
        id=row["id"],
        slug=row["slug"],
        status=row["status"],
        summary=row["summary"],
        turn_count=row["turn_count"],
        unread_count=row["unread_count"],
        recent_subjects=row["recent_subjects"],
        tags=row.get("tags", []) or [],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


@router.get("/threads/{thread_id}/export")
async def export_thread_route(thread_id: str) -> Response:
    """Reconstruct a human-readable markdown document from turns."""
    thread_id = normalize_thread_id(thread_id)
    thread = get_thread(thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    turns = get_thread_turns_asc(thread_id)

    lines: list[str] = [f"# Thread {thread['id']} - {thread['slug']}\n"]
    if thread.get("summary"):
        lines.append(f"> {thread['summary']}\n")
    lines.append(f"Status: {thread['status']}  |  Turns: {len(turns)}\n")

    for t in turns:
        lines.append("---\n")
        lines.append(
            f"## Turn {t['turn_number']} - {t['from_agent']} - {t['created_at']} UTC\n"
        )
        lines.append(f"**To:** {t['to_agent']}\n")
        if t.get("subject"):
            lines.append(f"**Subject:** {t['subject']}\n")
        lines.append(f"\n{t['body']}\n")
        atts = t.get("attachments")
        if atts:
            lines.append("\n**Attachments:**\n")
            for a in atts:
                size = f" ({a['size_bytes']} bytes)" if a.get("size_bytes") else ""
                lines.append(f"- `{a['filename']}`{size} — {a['path']}\n")

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{thread_id}-{thread["slug"]}.md"'
            )
        },
    )


@router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadDetail,
)
async def create_thread_route(body: ThreadCreate) -> ThreadDetail:
    """Create one thread using explicit or auto-generated thread identifiers."""
    if body.id is not None:
        body.id = normalize_thread_id(body.id)
    row = create_thread(
        thread_id=body.id,
        slug=body.slug,
        summary=body.summary,
        tags=body.tags,
        lifecycle_state=body.lifecycle_state,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Thread {body.id} already exists",
        )
    return _thread_detail(row)


@router.post(
    "/threads/with-turn",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadWithTurnCreated,
)
async def create_thread_with_turn_route(
    body: ThreadWithTurnCreate,
) -> ThreadWithTurnCreated:
    """Atomically create a thread and its first turn in one transaction."""
    if error_detail := post_continuation_misuse_error(body.slug, body.after_turn):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail,
        )
    if error_detail := turn_body_limit_error(
        body.body,
        allow_long_body=body.allow_long_body,
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None
    try:
        thread_row, turn_id, ts, turn_number = create_thread_with_turn(
            slug=body.slug,
            summary=body.summary,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=body.body,
            status=body.status,
            after_turn=body.after_turn,
            attachments=att_dicts,
            tags=body.tags,
            lifecycle_state=body.lifecycle_state,
        )
    except UnreadTurnsExist as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.to_detail(),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ThreadWithTurnCreated(
        thread=_thread_detail(thread_row),
        turn=TurnCreated(
            id=turn_id,
            thread=thread_row["id"],
            turn_number=turn_number,
            created_at=datetime.fromisoformat(ts),
        ),
    )


def _send_xor_violation(*, provided: list[str]) -> dict[str, object]:
    if provided:
        message = (
            "thread and new_slug are mutually exclusive — provide exactly one"
        )
    else:
        message = (
            "exactly one of thread or new_slug is required — neither was provided"
        )
    return {
        "error": message,
        "reason": "send_xor_violation",
        "provided": provided,
        "required": "exactly_one_of_thread_or_new_slug",
    }


def _send_with_sidecar(body: TurnSendCreate) -> TurnSendCreated:
    """E4 send path: thread id → sidecar write → turn insert."""
    from cortex_store.dispatch_ops._thread_sidecar import (
        SidecarWriteError,
        append_sidecar_pointer_line,
        write_thread_sidecar_for_send,
    )

    from ..db.connection import connect
    from ..db.turns import UnreadTurnsExist, insert_turn, mark_sender_unread_in_thread
    from ..events.lifecycle import emit_sidecar_orphaned, emit_sidecar_written

    if error_detail := sidecar_content_limit_error(body.sidecar_content or ""):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )

    has_new_slug = body.new_slug is not None
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None
    thread_id: str | None = None
    send_path: str

    if has_new_slug:
        if body.after_turn is not None and body.after_turn > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "after_turn > 0 is invalid on the new_slug (new-thread) path",
                    "reason": "after_turn_not_valid_on_new_thread",
                },
            )
        with connect() as conn:
            existing = conn.execute(
                "SELECT id FROM threads WHERE slug = ? LIMIT 1",
                (body.new_slug,),
            ).fetchone()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "slug_exists",
                        "slug": body.new_slug,
                        "existing_thread_id": existing["id"],
                        "message": (
                            f"A thread with slug {body.new_slug!r} already exists "
                            f"(thread {existing['id']}). "
                            "Use send(thread=<id>, ...) to continue it or choose "
                            "a different new_slug."
                        ),
                    },
                )
        thread_row = create_thread(
            thread_id=None,
            slug=body.new_slug,
            summary=body.summary,
            tags=body.tags or [],
            lifecycle_state=body.lifecycle_state,
        )
        if thread_row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create thread for sidecar send",
            )
        thread_id = thread_row["id"]
        send_path = "new_thread"
    else:
        thread_id = normalize_thread_id(body.thread)
        if get_thread(thread_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found",
            )
        if body.lifecycle_state is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": (
                        "lifecycle_state is only valid on the new_slug (new-thread) path"
                    ),
                    "reason": "lifecycle_state_not_valid_on_continue",
                },
            )
        send_path = "continue"

    assert thread_id is not None
    try:
        sidecar = write_thread_sidecar_for_send(
            thread=thread_id,
            subject=body.subject,
            content=body.sidecar_content or "",
            from_agent=body.from_agent,
            sidecar_slug=body.sidecar_slug,
        )
    except SidecarWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=sidecar_write_failed_envelope(
                thread_id=thread_id,
                error=str(exc),
            ),
        ) from exc

    final_body = append_sidecar_pointer_line(body.body, sidecar_uri=sidecar.uri)
    if error_detail := turn_body_limit_error(
        final_body,
        allow_long_body=body.allow_long_body,
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )

    effective_after = body.after_turn if body.after_turn and body.after_turn > 0 else None
    try:
        turn_id, ts, turn_number = insert_turn(
            thread=thread_id,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=final_body,
            status=body.status,
            after_turn=effective_after,
            attachments=att_dicts,
        )
    except UnreadTurnsExist as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.to_detail(),
        ) from exc
    except Exception as exc:
        emit_sidecar_orphaned(
            uri=sidecar.uri,
            error=str(exc),
            thread_id=thread_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "turn_insert_failed",
                "reason": "turn_insert_failed",
                "message": "Turn insert failed after sidecar write; sidecar file may be orphaned.",
                "retryable": True,
                "source": "agent_bus_store.send",
                "data": {"thread_id": thread_id, "sidecar_uri": sidecar.uri},
            },
        ) from exc

    marked_read = 0
    if body.mark_read:
        through = effective_after if effective_after else turn_number - 1
        marked_read = mark_sender_unread_in_thread(
            thread=thread_id,
            from_agent=body.from_agent,
            through_turn=through,
        )
    if body.close:
        close_thread(thread_id, mark_all_read=True)

    emit_sidecar_written(
        thread=thread_id,
        turn_number=turn_number,
        uri=sidecar.uri,
        sha256=sidecar.sha256,
        bytes_written=sidecar.body_chars,
    )

    thread_row = get_thread(thread_id)
    if thread_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return TurnSendCreated(
        send_path=send_path,  # type: ignore[arg-type]
        thread=_thread_detail(thread_row),
        turn=TurnCreated(
            id=turn_id,
            thread=thread_id,
            turn_number=turn_number,
            created_at=datetime.fromisoformat(ts),
        ),
        marked_read=marked_read,
        sidecar_uri=sidecar.uri,
        sidecar_sha256=sidecar.sha256,
    )


@router.post(
    "/threads/send",
    status_code=status.HTTP_201_CREATED,
    response_model=TurnSendCreated,
)
async def send_route(body: TurnSendCreate) -> TurnSendCreated:
    """Unified send: create new thread (new_slug) OR continue existing (thread)."""
    has_new_slug = body.new_slug is not None
    has_thread = bool(body.thread)
    if has_new_slug and has_thread:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_send_xor_violation(provided=["thread", "new_slug"]),
        )
    if not has_new_slug and not has_thread:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_send_xor_violation(provided=[]),
        )
    if body.sidecar_content is not None:
        return _send_with_sidecar(body)
    if error_detail := turn_body_limit_error(
        body.body,
        allow_long_body=body.allow_long_body,
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None

    if has_new_slug:
        if body.after_turn is not None and body.after_turn > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "after_turn > 0 is invalid on the new_slug (new-thread) path",
                    "reason": "after_turn_not_valid_on_new_thread",
                },
            )
        try:
            thread_row, turn_id, ts, turn_number = create_thread_with_turn(
                slug=body.new_slug,
                summary=body.summary,
                from_agent=body.from_agent,
                to_agent=body.to,
                subject=body.subject,
                body=body.body,
                status=body.status,
                after_turn=0,
                attachments=att_dicts,
                tags=body.tags or [],
                lifecycle_state=body.lifecycle_state,
                strict_slug=True,
            )
        except SlugExists as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "slug_exists",
                    "slug": e.slug,
                    "existing_thread_id": e.existing_thread_id,
                    "message": (
                        f"A thread with slug {e.slug!r} already exists "
                        f"(thread {e.existing_thread_id}). "
                        "Use send(thread=<id>, ...) to continue it or choose "
                        "a different new_slug."
                    ),
                },
            )
        except UnreadTurnsExist as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=e.to_detail(),
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return TurnSendCreated(
            send_path="new_thread",
            thread=_thread_detail(thread_row),
            turn=TurnCreated(
                id=turn_id,
                thread=thread_row["id"],
                turn_number=turn_number,
                created_at=datetime.fromisoformat(ts),
            ),
        )

    if body.lifecycle_state is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": (
                    "lifecycle_state is only valid on the new_slug (new-thread) path"
                ),
                "reason": "lifecycle_state_not_valid_on_continue",
            },
        )

    thread_id = normalize_thread_id(body.thread)
    try:
        thread_row, turn_id, ts, turn_number, marked_read = create_turn(
            thread_id=thread_id,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=body.body,
            status=body.status,
            after_turn=body.after_turn,
            attachments=att_dicts,
            close=body.close,
            mark_read=body.mark_read,
        )
    except UnreadTurnsExist as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.to_detail(),
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return TurnSendCreated(
        send_path="continue",
        thread=_thread_detail(thread_row),
        turn=TurnCreated(
            id=turn_id,
            thread=thread_id,
            turn_number=turn_number,
            created_at=datetime.fromisoformat(ts),
        ),
        marked_read=marked_read,
    )


@router.patch(
    "/threads/{thread_id}/close",
    response_model=ThreadDetail,
)
async def close_thread_route(thread_id: str, body: ThreadClose) -> ThreadDetail:
    """Atomically close a thread: mark all turns read + set status + summary."""
    thread_id = normalize_thread_id(thread_id)
    row = close_thread(
        thread_id, summary=body.summary, mark_all_read=body.mark_all_read
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.patch(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
)
async def update_thread_route(thread_id: str, body: ThreadUpdate) -> ThreadDetail:
    thread_id = normalize_thread_id(thread_id)
    row = update_thread(
        thread_id, status=body.status, summary=body.summary, tags=body.tags
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/rename",
    response_model=ThreadDetail,
)
async def rename_thread_route(thread_id: str, body: ThreadRename) -> ThreadDetail:
    """Rename a thread id while preserving associated turns and messages."""
    thread_id = normalize_thread_id(thread_id)
    try:
        row = rename_thread(thread_id, new_id=body.new_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/dispatch-admit",
    response_model=ThreadDetail,
)
async def dispatch_admit_route(thread_id: str, body: DispatchAdmit) -> ThreadDetail:
    """Register a pipeline dispatch link and advance lifecycle state.

    - If bus_lifecycle_state == "pending": transitions to "admitted".
    - If bus_lifecycle_state is NULL: link registered, no lifecycle transition
      (documented coverage gap — pre-create with lifecycle_state="pending" for
      full recovery support).
    - Returns 409 when thread is in a terminal state.
    """
    thread_id = normalize_thread_id(thread_id)
    try:
        row = admit_dispatch(
            thread_id=thread_id,
            execution_id=body.execution_id,
            pipeline_id=body.pipeline_id,
            caller_agent=body.caller_agent,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/dispatch-claim-and-post",
    response_model=ThreadDetail,
)
async def dispatch_claim_and_post_route(
    thread_id: str, body: DispatchClaimAndPost
) -> ThreadDetail:
    """Atomically claim a pending-empty shell and post the first pointer turn.

    Checks pending+turn_count==0, admits, inserts the pointer turn, and
    transitions admitted->active in one SQLite write transaction.

    Returns 409 with code=pending_shell_contention when the CAS guard fails.
    """
    thread_id = normalize_thread_id(thread_id)
    try:
        row = claim_and_post_turn(
            thread_id=thread_id,
            execution_id=body.execution_id,
            pipeline_id=body.pipeline_id,
            caller_agent=body.caller_agent,
            from_agent=body.from_agent,
            to_agent=body.to_agent,
            subject=body.subject,
            body=body.body,
        )
    except PendingShellContention as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "pending_shell_contention", "message": str(exc)},
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/dispatch-terminate",
    response_model=ThreadDetail,
)
async def dispatch_terminate_route(
    thread_id: str, body: DispatchTerminate
) -> ThreadDetail:
    """Mark dispatch link terminal_status (completed or failed)."""
    from agent_bus_store.disposition import maybe_auto_close_after_dispatch_terminate

    thread_id = normalize_thread_id(thread_id)
    row = terminate_dispatch(
        thread_id=thread_id,
        terminal_status=body.terminal_status,
        execution_id=body.execution_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    closed = maybe_auto_close_after_dispatch_terminate(
        thread_id,
        terminal_status=body.terminal_status,
        explicit_bus_lifecycle=body.bus_lifecycle,
    )
    if closed is not None:
        row = closed
    return _thread_detail(row)


def _triage_confirm_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": code,
            "message": message,
            "retryable": code == "confirm_token_expired",
            "source": "agent_bus_store.triage",
        },
    )


def _emit_triage_event(signal: str, payload: dict[str, object]) -> None:
    from ..events.publisher import emit

    emit(signal, payload, role="coordination")


@router.post(
    "/threads/triage",
    response_model=ThreadTriageDryRun | ThreadTriageExecuted,
)
async def triage_threads_route(body: ThreadTriageRequest) -> ThreadTriageDryRun | ThreadTriageExecuted:
    """Bulk inbox hygiene — preview (dry_run) or execute with confirm_token."""
    if floor := triage_floor_error(body.action, body.older_than):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=floor)

    try:
        activity_cutoff = parse_older_than(body.older_than)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_older_than",
                "message": str(exc),
                "retryable": False,
                "source": "agent_bus_store.triage",
            },
        ) from exc

    all_rows, total_candidates = list_triage_candidates(
        agent=body.from_agent,
        activity_cutoff=activity_cutoff,
        action=body.action,
        status=body.status.value if body.status is not None else None,
    )
    capped = total_candidates > TRIAGE_THREAD_CAP
    preview_rows = all_rows[:TRIAGE_THREAD_CAP]
    candidate_ids = [str(row["id"]) for row in preview_rows]

    if body.dry_run:
        confirm_token, expires_at = issue_triage_confirm_token(
            agent=body.from_agent,
            action=body.action,
            older_than=body.older_than,
            status=body.status.value if body.status is not None else None,
            candidate_ids=candidate_ids,
        )
        _emit_triage_event(
            "mcp.agentbus.triage.dry_run",
            {
                "agent": body.from_agent,
                "filter": {
                    "older_than": body.older_than,
                    "status": body.status.value if body.status else None,
                    "action": body.action,
                },
                "total_candidates": total_candidates,
                "capped": capped,
                "confirm_token_id": confirm_token,
            },
        )
        return ThreadTriageDryRun(
            candidates=[
                ThreadTriageCandidate(
                    id=row["id"],
                    slug=row["slug"],
                    last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
                    unread_count=int(row["unread_count"]),
                )
                for row in preview_rows
            ],
            total_candidates=total_candidates,
            capped=capped,
            confirm_token=confirm_token,
            expires_at=expires_at,
        )

    if not body.confirm_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "confirm_token_required",
                "message": "dry_run=false requires confirm_token from the preview call",
                "retryable": True,
                "source": "agent_bus_store.triage",
            },
        )

    token_status = consume_triage_confirm_token(
        token_id=body.confirm_token,
        agent=body.from_agent,
        action=body.action,
        older_than=body.older_than,
        status=body.status.value if body.status is not None else None,
        candidate_ids=candidate_ids,
    )
    if token_status == "invalid":
        raise _triage_confirm_error(
            "confirm_token_invalid",
            "confirm_token is invalid or already used",
        )
    if token_status == "expired":
        raise _triage_confirm_error(
            "confirm_token_expired",
            "confirm_token expired (10 minute TTL); re-run dry_run",
        )
    if token_status == "filter_mismatch":
        raise _triage_confirm_error(
            "confirm_token_filter_mismatch",
            "confirm_token does not match the current filter or candidate set",
        )

    marked_read = 0
    closed = 0
    if body.action == "mark_read":
        marked_read = execute_triage_mark_read(
            agent=body.from_agent,
            thread_ids=candidate_ids,
        )
    else:
        closed = execute_triage_close(thread_ids=candidate_ids)

    _emit_triage_event(
        "mcp.agentbus.triage.executed",
        {
            "agent": body.from_agent,
            "action": body.action,
            "thread_count": len(candidate_ids),
            "confirm_token_id": body.confirm_token,
            "marked_read": marked_read,
            "closed": closed,
        },
    )
    return ThreadTriageExecuted(
        action=body.action,
        thread_count=len(candidate_ids),
        marked_read=marked_read,
        closed=closed,
        confirm_token_id=body.confirm_token,
    )


@router.delete("/threads/{thread_id}")
async def delete_thread_route(
    thread_id: str,
    force: bool = Query(False),
) -> dict[str, Any]:
    """Delete a thread, requiring force when acknowledged turns already exist."""
    thread_id = normalize_thread_id(thread_id)
    try:
        return delete_thread(thread_id, force=force)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    except ThreadHasReadTurns as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Thread has {e.read_count} read turn(s) - use force=true",
        )
