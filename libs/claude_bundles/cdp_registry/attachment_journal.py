"""Attachment journal — U-scan, ``attachment_observed`` append, and fold replay.

Owns the chat_url attachment axis of a CDP registration: the uniqueness scan
over host-listable rows, the durable ``attachment_observed`` line, and the fold
that replays observation / bind / stand-down / detach lines onto ``active.json``
and stamps ``attached_at`` under seat authority. Callers are the cdp_ask harvest
latch (``cdp_ask.runner``), session-address binding
(``cdp_registry.session_address``), the paste stand-down token
(``cdp_ask.cse_session_paste``) and ``detach`` (``cdp_registry.lifecycle``).

Every reader here consumes ``journal_projection.read_fold_rows`` — the
checkpoint-plus-tail projection of ``registry.jsonl`` — so an observe, bind, or
paste parses only fold-relevant rows, not the whole history (a:36920). Replay
mutates only ids already present in the active map; ids hygiene dropped are
never resurrected (a:36906).
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from universal_protocol.errors import ProtocolError

from claude_bundles import cdp_registry_store as _store
from claude_bundles.cdp_registry.journal_projection import read_fold_rows
from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES
from claude_bundles.cse_url import normalize_cse_url


def host_listable_ids_for_norm_url(
    active: dict[str, dict[str, Any]],
    norm_url: str,
    *,
    exclude_registration_id: str | None = None,
) -> list[str]:
    """Registration ids whose host-listable row's ``chat_url`` normalizes to *norm_url*.

    Only rows with a status in ``_HOST_LISTABLE_STATUSES`` count; released or
    reaped rows never block a rebind.
    """
    out: list[str] = []
    for rid, row in active.items():
        if not isinstance(row, dict):
            continue
        if row.get("status") not in _HOST_LISTABLE_STATUSES:
            continue
        if exclude_registration_id and rid == exclude_registration_id:
            continue
        bound = str(row.get("chat_url") or "").strip()
        if bound and normalize_cse_url(bound) == norm_url:
            out.append(str(rid))
    return out


def assert_attachment_unique(
    active: dict[str, dict[str, Any]],
    chat_url: str,
    *,
    registration_id: str,
) -> None:
    """U-scan: raise ``attachment.conflict`` when another host-listable row holds *chat_url*."""
    norm = normalize_cse_url(chat_url)
    if not norm:
        return
    conflicts = host_listable_ids_for_norm_url(
        active, norm, exclude_registration_id=registration_id
    )
    if conflicts:
        raise ProtocolError(
            code="attachment.conflict",
            message=(
                f"chat_url already attached to {conflicts!r}; "
                f"refusing bind for {registration_id!r}"
            ),
            source="rpc",
            retryable=False,
            data={"chat_url": chat_url, "registration_ids": conflicts},
        )


def append_attachment_journal(
    *,
    registration_id: str,
    chat_url: str,
    attach_proof: str,
    execution_id: str | None = None,
) -> None:
    """Append one ``attachment_observed`` line after U-scan on the live snapshot."""
    with _store.ports_lock():
        active = _store.load_active()
        assert_attachment_unique(active, chat_url, registration_id=registration_id)
        _store.append_log(
            "attachment_observed",
            {
                "registration_id": registration_id,
                "chat_url": chat_url,
                "attach_proof": attach_proof,
                "execution_id": execution_id,
                "observed_at": time.time(),
            },
        )


def _has_later_session_address_bound(
    log: list[dict[str, Any]], registration_id: str, after_index: int
) -> bool:
    """True when *registration_id* has ``session_address_bound`` after *after_index*."""
    rid = str(registration_id or "").strip()
    if not rid:
        return False
    for record in log[after_index + 1 :]:
        if str(record.get("registration_id") or "").strip() != rid:
            continue
        if str(record.get("event") or "") == "session_address_bound":
            return True
    return False


def _apply_attachment_observed(
    active: dict[str, dict[str, Any]],
    record: dict[str, Any],
    *,
    skip_chat_url: bool = False,
) -> None:
    """Replay one ``attachment_observed`` line onto *active* (chat_url only).

    Ids hygiene has already dropped are skipped: replaying the full journal used
    to mint 1782 status-less skeleton rows into ``active.json`` (a:36906).
    """
    reg_id = str(record.get("registration_id") or "").strip()
    url = str(record.get("chat_url") or "").strip()
    if not reg_id or not url or reg_id not in active:
        return
    row = dict(active[reg_id])
    if not skip_chat_url:
        row["chat_url"] = url
    if record.get("execution_id"):
        row["execution_id"] = record.get("execution_id")
    active[reg_id] = row


def _apply_session_address_bound(
    active: dict[str, dict[str, Any]], record: dict[str, Any]
) -> None:
    """Replay one ``session_address_bound`` journal line (authoritative chat_url)."""
    reg_id = str(record.get("registration_id") or "").strip()
    url = str(record.get("chat_url") or "").strip()
    if not reg_id or not url or reg_id not in active:
        return
    row = dict(active[reg_id])
    row["chat_url"] = url
    if record.get("execution_id"):
        row["execution_id"] = record.get("execution_id")
    if record.get("target_id"):
        row["target_id"] = record.get("target_id")
    active[reg_id] = row


def _apply_attachment_bound(
    active: dict[str, dict[str, Any]], record: dict[str, Any]
) -> None:
    reg_id = str(record.get("registration_id") or "").strip()
    if not reg_id or reg_id not in active:
        return
    row = dict(active[reg_id])
    if record.get("attached_at") is not None:
        row["attached_at"] = record.get("attached_at")
    if record.get("attach_proof"):
        row["attach_proof"] = record.get("attach_proof")
    active[reg_id] = row


def _apply_standdown_pasted(
    active: dict[str, dict[str, Any]], record: dict[str, Any]
) -> None:
    reg_id = str(record.get("registration_id") or "").strip()
    if not reg_id or reg_id not in active:
        return
    row = dict(active[reg_id])
    row["standdown_pasted_at"] = record.get("pasted_at") or record.get("ts")
    active[reg_id] = row


def _detached_after_ts(
    log: list[dict[str, Any]], registration_id: str, ts: float
) -> bool:
    """True when *log* holds a ``detached`` line for *registration_id* at or after *ts*."""
    for record in log:
        if str(record.get("registration_id") or "") != registration_id:
            continue
        if str(record.get("event") or "") != "detached":
            continue
        detached_ts = record.get("detached_at") or record.get("ts") or 0.0
        try:
            if float(detached_ts) >= float(ts):
                return True
        except (TypeError, ValueError):
            return True
    return False


def has_attachment_observed(registration_id: str, chat_url: str) -> bool:
    """True when an ``attachment_observed`` line already pairs *registration_id* with *chat_url*.

    Takes ``ports_lock`` itself (callers hold no lock) so the projection pass
    that precedes the read is single-writer.
    """
    norm = normalize_cse_url(chat_url)
    with _store.ports_lock():
        log = read_fold_rows()
    for record in log:
        if str(record.get("event") or "") != "attachment_observed":
            continue
        if str(record.get("registration_id") or "") != registration_id:
            continue
        url = str(record.get("chat_url") or "").strip()
        if url and normalize_cse_url(url) == norm:
            return True
    return False


def has_standdown_token(registration_id: str) -> bool:
    """True when paste appended ``standdown_pasted`` or ``standdown_unreachable``.

    Caller holds ``ports_lock`` (``lifecycle.detach`` calls this inside its lock;
    ``ports_lock`` is flock-based and does not nest).
    """
    rid = str(registration_id or "").strip()
    if not rid:
        return False
    for record in read_fold_rows():
        if str(record.get("registration_id") or "") != rid:
            continue
        ev = str(record.get("event") or "")
        if ev in {"standdown_pasted", "standdown_unreachable"}:
            return True
    return False


def fold_attachment_journal(
    active: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Replay attachment journal lines; stamp authority fields when permitted.

    Replays the fold projection (not the full journal) onto *active* or the
    on-disk map: ``detached`` pops the id, observation / bind / stand-down lines
    update the row, and the last un-bound observation of a still-present id is
    stamped ``attached_at`` and journaled as ``attachment_bound`` when this
    process is the seat authority. Writes the result to ``active.json`` and
    returns it.
    """
    from claude_bundles import cdp_registry_events as _events

    with _store.ports_lock():
        state = dict(_store.load_active() if active is None else active)
        last_observation: dict[str, dict[str, Any]] = {}
        log = read_fold_rows()

        for idx, record in enumerate(log):
            ev = str(record.get("event") or "")
            reg_id = str(record.get("registration_id") or "").strip()
            if ev == "detached":
                state.pop(reg_id, None)
                last_observation.pop(reg_id, None)
                continue
            if ev == "attachment_observed" and reg_id:
                last_observation[reg_id] = record
                skip_url = _has_later_session_address_bound(log, reg_id, idx)
                _apply_attachment_observed(state, record, skip_chat_url=skip_url)
            elif ev == "session_address_bound" and reg_id:
                _apply_session_address_bound(state, record)
            elif ev == "attachment_bound" and reg_id:
                _apply_attachment_bound(state, record)
            elif ev == "standdown_pasted" and reg_id:
                _apply_standdown_pasted(state, record)

        for reg_id, obs in last_observation.items():
            if reg_id not in state:
                continue
            row = state[reg_id]
            if row.get("attached_at"):
                continue
            obs_ts = obs.get("observed_at") or obs.get("ts") or time.time()
            if _detached_after_ts(log, reg_id, float(obs_ts)):
                continue
            if not _store.is_seat_authority():
                continue
            updated = dict(row)
            updated["attached_at"] = obs_ts
            updated["attach_proof"] = obs.get("attach_proof")
            state[reg_id] = updated
            _store.append_log(
                "attachment_bound",
                {
                    "registration_id": reg_id,
                    "chat_url": obs.get("chat_url"),
                    "attach_proof": obs.get("attach_proof"),
                    "attached_at": obs_ts,
                },
            )
            with contextlib.suppress(Exception):
                _events.emit(
                    _events.cdp_attachment_bound(
                        registration_id=reg_id,
                        chat_url=str(obs.get("chat_url") or ""),
                        attach_proof=str(obs.get("attach_proof") or ""),
                        parent_thread=str(row.get("parent_thread") or "") or None,
                    )
                )

        _store.write_active(state)
        return state
