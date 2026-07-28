"""Compose charter arc closeout bodies for state-close."""

from __future__ import annotations

from typing import Any

from cortex_store.dispatch_ops._thread_sidecar import write_thread_sidecar_for_send

from scripts.model_manager import observation_event_conveyor as conv_events

from . import bus_client, conveyor
from .checkpoint_schema import (
    aggregate_what_happened_plain,
    extract_remaining_work,
)
from .closeout_render import render_closeout
from .friction_ledger import build_ledger
from .harvest import completed_windows
from .window_terminal_contract import is_tip_class


def _checkpoint_bodies(turns: list[dict[str, Any]]) -> list[str]:
    bodies: list[str] = []
    for _adm, checkpoint in completed_windows(turns):
        bodies.append(str(checkpoint.get("body") or ""))
    if not bodies:
        for turn in sorted(turns, key=lambda t: int(t.get("turn_number") or 0)):
            if is_tip_class(
                str(turn.get("subject") or ""), body=str(turn.get("body") or "")
            ):
                bodies.append(str(turn.get("body") or ""))
    return bodies


def compose_closeout_body(
    *,
    root_id: str,
    root_subject: str,
    root_tags: list[str] | None,
    turns: list[dict[str, Any]],
    reason: str,
    checkpoint_turn: int | None,
) -> str:
    """Build rendered closeout markdown for sidecar + bus close summary."""
    bodies = _checkpoint_bodies(turns)
    what_happened = aggregate_what_happened_plain(bodies)
    final_body = bodies[-1] if bodies else ""
    where_left = extract_remaining_work(final_body)
    ledger = build_ledger(
        root_id,
        root_tags=root_tags,
        on_conveyor_fn=lambda fid, slug: conveyor.is_on_conveyor(fid, slug),
        stale_fn=conveyor.is_stale_unenrolled,
    )
    return render_closeout(
        root_id=root_id,
        root_subject=root_subject,
        window_count=len(bodies),
        reason=reason,
        what_happened=what_happened,
        where_left=where_left,
        ledger=ledger,
        checkpoint_turn=checkpoint_turn,
    )


def write_closeout_sidecar(*, root_id: str, body: str, reason: str) -> str:
    """Persist closeout on the charter root thread; return cortex URI."""
    subject = f"Charter closeout — {reason}"
    result = write_thread_sidecar_for_send(
        thread=root_id,
        subject=subject,
        content=body,
        from_agent="charter-runner",
        sidecar_slug="closeout",
    )
    return result.uri


async def emit_closeout_rendered(
    *,
    root_id: str,
    reason: str,
    sidecar_uri: str,
    window_count: int,
) -> None:
    """Emit manage.charter.closeout.rendered."""
    ledger = build_ledger(
        root_id,
        on_conveyor_fn=lambda fid, slug: conveyor.is_on_conveyor(fid, slug),
        stale_fn=conveyor.is_stale_unenrolled,
    )
    await conv_events.emit_manage_charter_closeout_rendered(
        root=root_id,
        reason=reason,
        sidecar_uri=sidecar_uri,
        window_count=window_count,
        friction_count=len(ledger),
    )


async def prepare_state_close_summary(
    *,
    root_id: str,
    reason: str,
    checkpoint_turn: int | None,
    turns: list[dict[str, Any]] | None = None,
) -> tuple[str, str | None]:
    """Return (rendered_summary, sidecar_uri) for a charter state-close.

    Bus close ``summary`` preserves the standing so-what as ``DONE — …``; the
    full rendered body still goes to the closeout sidecar.
    """
    from pager_notify.so_what import compose_done_summary

    if turns is None:
        turns = await bus_client.fetch_turns(root_id)
    detail = await bus_client.fetch_thread(root_id)
    tags = list(detail.get("tags") or [])
    prior_so_what = str(detail.get("summary") or "")
    if isinstance(detail.get("thread"), dict):
        nested = detail.get("thread") or {}
        tags = list(nested.get("tags") or tags)
        if not prior_so_what:
            prior_so_what = str(nested.get("summary") or "")
    subject = prior_so_what or str(detail.get("slug") or root_id)
    body = compose_closeout_body(
        root_id=root_id,
        root_subject=subject,
        root_tags=tags,
        turns=turns,
        reason=reason,
        checkpoint_turn=checkpoint_turn,
    )
    window_count = len(_checkpoint_bodies(turns))
    sidecar_uri = write_closeout_sidecar(root_id=root_id, body=body, reason=reason)
    await emit_closeout_rendered(
        root_id=root_id,
        reason=reason,
        sidecar_uri=sidecar_uri,
        window_count=window_count,
    )
    close_summary = compose_done_summary(prior_so_what, reason=reason)
    return close_summary, sidecar_uri


__all__ = [
    "compose_closeout_body",
    "prepare_state_close_summary",
    "write_closeout_sidecar",
]
