"""Seal a predecessor Cowork transcript onto continuity tape before detach.

cdp_ask detach and typed-ack harvest call ``seal_predecessor_cse_to_tape``.
A live Chrome port is scraped and posted to Cortex ``/session-journals/close``
(hub UDS, or HTTP when that socket is absent on the satellite). Failures emit ``cdp.tape.seal.failed`` and raise
``standdown.tape_seal_failed`` so the listener stays attached. A dead port
or a missing registry row emits ``cdp.tape.seal.degraded`` and returns a
degraded dict; this module does not detach.
"""

from __future__ import annotations

import os
from typing import Any

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

from cdp_ask.cse_session_events import emit
from cdp_ask.execution_store import ExecutionStore


@event_factory
def cdp_tape_seal_failed(
    *,
    registration_id: str,
    error_type: str,
    detail: str,
    stage: str,
) -> Event:
    """Report that a predecessor tape seal failed before the seat detached."""
    return Event(
        signal="cdp.tape.seal.failed",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "error_type": error_type,
            "detail": detail,
            "stage": stage,
        },
    )


def _emit_seal_failed(registration_id: str, exc: BaseException, stage: str) -> None:
    emit(
        cdp_tape_seal_failed(
            registration_id=registration_id,
            error_type=type(exc).__name__,
            detail=str(exc),
            stage=stage,
        )
    )


@event_factory
def cdp_tape_seal_degraded(
    *,
    registration_id: str,
    reason: str,
) -> Event:
    """Report a predecessor seal that skipped scrape because the host is gone."""
    return Event(
        signal="cdp.tape.seal.degraded",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "reason": reason,
            "stage": reason,
        },
    )


def _emit_seal_degraded(registration_id: str, reason: str) -> None:
    emit(
        cdp_tape_seal_degraded(
            registration_id=registration_id,
            reason=reason,
        )
    )


def cortex_session_close_url() -> str:
    """URL for ``/session-journals/close``.

    Hub ``cdp_ask`` reaches cortex-api on the local UDS. Jupiter ``cdp_ask``
    has no ``/tmp/universal-protocol/cortex-api.sock`` (that inode is created
    only by hub cortex-api). Explicit ``CORTEX_API_HTTP`` wins; otherwise a
    missing socket falls back to HTTP on the ``EVENTS_INGEST_TCP`` host.
    """
    explicit = os.environ.get("CORTEX_API_HTTP", "").strip()
    if explicit:
        if "://" not in explicit:
            explicit = f"http://{explicit}"
        return explicit.rstrip("/")
    sock = os.environ.get("CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock")
    if os.path.exists(sock):
        return f"unix://{sock}"
    ingest = os.environ.get("EVENTS_INGEST_TCP", "").strip()
    host = ingest.rpartition(":")[0].strip() if ":" in ingest else ""
    if host:
        port = os.environ.get("CORTEX_API_HTTP_PORT", "").strip() or "8202"
        return f"http://{host}:{port}"
    return f"unix://{sock}"


def _post_session_close(payload: dict[str, Any]) -> Any:
    """POST a session-close body to Cortex. Tests replace this."""
    from transport_utils import make_sync_client

    with make_sync_client(cortex_session_close_url(), timeout=30.0) as client:
        return client.post("/session-journals/close", json=payload)


async def _scrape_predecessor_turns(port: int, chat_url: str) -> list[dict[str, Any]]:
    """Scrape the live Cowork page for one predecessor port."""
    from claude_bundles.cse_turns_harvest import harvest_turns
    from claude_bundles.skills_ui_panel import connect_cdp

    from cdp_ask.cse_session_harvest import HARVEST_HARD_CAP
    from cdp_ask.cse_session_harvest_scrape import pick_page_for_chat_url

    pw, _browser, ctx, page = await connect_cdp(f"http://127.0.0.1:{port}")
    try:
        page = await pick_page_for_chat_url(ctx, chat_url, fallback=page)
        raw = await harvest_turns(page, limit=HARVEST_HARD_CAP)
    finally:
        await pw.stop()
    turns = raw.get("turns") if isinstance(raw, dict) else None
    return [row for row in (turns or []) if isinstance(row, dict)]


def _succession_close_payload(
    row: dict[str, Any],
    *,
    registration_id: str,
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a SessionCloseRequest body that passes the verbatim close gates."""
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    from chat_harvest.messages import build_harvest_envelope, turns_to_messages
    from continuity_tape.messages import envelope_wire_dict

    parent_thread = str(row.get("parent_thread") or row.get("seat_lane") or "").strip()
    chat_url = str(row.get("chat_url") or "").strip()
    started_at = float(row.get("started_at") or row.get("seat_bound_at") or 0.0)
    cse_id = urlsplit(chat_url).path.rstrip("/").split("/")[-1] or registration_id[:8]
    opened = datetime.fromtimestamp(started_at, tz=UTC)
    session_id = f"web-anthropic-{opened:%Y-%m-%d-%H%M%S}-{registration_id[:3].lower()}"
    summary = (
        f"Succession harvest seal for Cowork seat {registration_id[:8]} "
        f"on lane {parent_thread}."
    )
    session_summary_md = (
        "## Session Summary\n\n"
        f"Predecessor Cowork CSE (`{chat_url}`, registration `{registration_id}`) "
        f"sealed to continuity tape before detach on lane `{parent_thread}`.\n\n"
        "**Decisions:** (absent — succession harvest before CSE detach)\n"
        "**Open items:** (absent — succession harvest before CSE detach)\n"
    )
    harvested_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    envelope = build_harvest_envelope(
        site="claude",
        conversation_id=cse_id,
        url=chat_url,
        messages=turns_to_messages(turns),
        harvested_at=harvested_at,
        streaming=False,
    )
    return {
        "session_id": session_id,
        "agent": "web-anthropic",
        "summary": summary,
        "session_summary_md": session_summary_md,
        "transcript_depth": "verbatim",
        "transcript_messages": envelope_wire_dict(envelope),
        "closed_by": "succession",
        "succession_seal_authority": True,
        "entity_ids": [f"agent-bus:{parent_thread}"],
        "stamp_thread": parent_thread,
        "stamp_surface": "claude_ai",
        "conversation_uuid": cse_id,
    }


async def seal_predecessor_cse_to_tape(
    registration_id: str,
    *,
    store: ExecutionStore | None = None,
) -> dict[str, Any]:
    """Seal a predecessor Cowork transcript onto continuity tape before detach.

    A listening Chrome port is scraped and posted to Cortex ``/session-journals/close``.
    HTTP errors, timeouts, and a missing ``transcript_path`` emit
    ``cdp.tape.seal.failed`` and raise ``standdown.tape_seal_failed``, leaving
    the listener up. A dead port or missing row emits ``cdp.tape.seal.degraded``
    and returns a degraded dict so detach of a gone host can proceed. This function
    does not take ``ports_lock`` and does not detach. ``store`` is accepted for
    the detach route's call shape and is not required for the registry read.
    """
    from claude_bundles import cdp_lane
    from claude_bundles import cdp_registry_store as registry_store
    from universal_protocol.errors import ProtocolError

    del store
    rid = (registration_id or "").strip()
    row = registry_store.load_active().get(rid)
    if not isinstance(row, dict):
        _emit_seal_degraded(rid, "missing_row")
        return {"degraded": True, "registration_id": rid, "reason": "missing_row"}
    port = row.get("port")
    if not isinstance(port, int) or not cdp_lane.is_listening(port):
        _emit_seal_degraded(rid, "listener_down")
        return {
            "degraded": True,
            "registration_id": rid,
            "transcript_path": None,
            "reason": "listener_down",
        }
    chat_url = str(row.get("chat_url") or "").strip()
    try:
        turns = await _scrape_predecessor_turns(port, chat_url)
        from chat_harvest.archive import archive_chat_transcript
        from chat_harvest.models import ChatTurn

        cse_id = chat_url.rstrip("/").split("/")[-1]
        archive_turns = [
            ChatTurn(
                author=row_turn.get("author") or "assistant",
                ordinal=int(row_turn.get("ordinal") or index + 1),
                text=str(row_turn.get("text") or ""),
                source="dom",
            )
            for index, row_turn in enumerate(turns)
            if row_turn.get("author") in {"user", "assistant"}
        ]
        from chat_harvest.archive import ArchiveConflictError

        try:
            archive_chat_transcript(
                site="claude",
                conversation_id=cse_id,
                url=chat_url,
                turns=archive_turns,
                streaming=False,
            )
        except ArchiveConflictError:
            archive_chat_transcript(
                site="claude",
                conversation_id=cse_id,
                url=chat_url,
                turns=archive_turns,
                streaming=False,
                supersede=True,
            )
        payload = _succession_close_payload(row, registration_id=rid, turns=turns)
        response = _post_session_close(payload)
    except ProtocolError:
        raise
    except Exception as exc:
        _emit_seal_failed(rid, exc, "close")
        raise ProtocolError(
            code="standdown.tape_seal_failed",
            message=f"predecessor tape seal failed: {type(exc).__name__}: {exc}",
            source="rpc",
            retryable=True,
            data={"registration_id": rid},
        ) from exc
    status = getattr(response, "status_code", None)
    body: dict[str, Any] = {}
    json_failed = False
    if status == 200:
        try:
            parsed = response.json()
        except Exception as exc:
            _emit_seal_failed(rid, exc, "response_json")
            json_failed = True
            parsed = None
        if isinstance(parsed, dict):
            body = parsed
    path = str(body.get("transcript_path") or "").strip()
    already = bool(body.get("already_closed"))
    journal_row_id = body.get("journal_row_id")
    seal_refused = (
        status != 200 or not path or (journal_row_id in (None, "") and not already)
    )
    close_url = cortex_session_close_url()
    status_exc: RuntimeError | None = None
    if seal_refused and not json_failed:
        status_exc = RuntimeError(f"status_code={status}")
        _emit_seal_failed(rid, status_exc, "cortex_status")
    if seal_refused:
        if status_exc is None:
            status_exc = RuntimeError(f"status_code={status}")
        raise ProtocolError(
            code="standdown.tape_seal_failed",
            message=(
                "stand-down tape seal failed before detach: "
                f"{type(status_exc).__name__}: {status_exc} url={close_url}"
            ),
            source="rpc",
            retryable=True,
            data={
                "registration_id": rid,
                "status_code": status,
                "transcript_path": path or None,
            },
        )
    return {
        "registration_id": rid,
        "journal_row_id": journal_row_id,
        "transcript_path": path,
        "already_closed": already,
    }
