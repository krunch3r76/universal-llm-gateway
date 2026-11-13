"""Detect agent-bus threads that are houseless mailboxes (first-seat birth lanes).

Predicate (mailbox tag + structural):
- ``mailbox`` tag present on the bus thread
- folded ``parent_thread`` is empty
- ``role:root`` is absent (not an orchestration house spine)
- generate ``mission_kind`` is not ``hop`` (enforced by caller)

Does not require a ``houseless`` registry purpose tag; auto-tagging is a separate
ruling.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import get_thread, normalize_thread_id
from agent_bus_store.thread_classification import ROLE_ROOT_TAG


@dataclass(frozen=True, slots=True)
class MailboxThreadResolveError(Exception):
    """Admit-time mailbox resolve failure (maps to HTTP 422)."""

    code: str
    field: str = "mailbox_thread"
    reason: str = ""

    def __str__(self) -> str:
        return self.reason or self.code


def is_houseless_mailbox_thread(thread_id: str) -> bool:
    """Return True when *thread_id* names a parentless work mailbox thread."""
    tid = normalize_thread_id(thread_id)
    if not tid:
        return False
    try:
        detail = get_thread(tid)
    except Exception:  # noqa: BLE001 — seating bind is fail-soft
        return False
    if detail is None:
        return False
    if str(detail.get("parent_thread") or "").strip():
        return False
    tags = detail.get("tags") or []
    if ROLE_ROOT_TAG in tags:
        return False
    return "mailbox" in tags


def _houseless_threads_for_slug(slug: str) -> list[str]:
    slug = (slug or "").strip()
    if not slug:
        return []
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT id FROM threads WHERE slug = ? COLLATE NOCASE",
                (slug,),
            ).fetchall()
    except Exception:  # noqa: BLE001 — admit resolve is fail-closed
        return []
    return [
        str(row["id"])
        for row in rows
        if is_houseless_mailbox_thread(str(row["id"]))
    ]


def resolve_mailbox_thread_at_admit(
    *,
    mailbox_thread: str | None,
    caller_agent: str | None,
    thread_id: str | None,
) -> str:
    """Resolve mailbox lane at generate admit (fail-closed).

    An explicit ``mailbox_thread`` that is not a houseless mailbox →
    ``mailbox_thread_invalid``. Ambiguous slug → ``mailbox_thread_ambiguous``.
    No resolution → ``mailbox_thread_required``.
    """
    raw_mailbox = (mailbox_thread or "").strip()
    if raw_mailbox:
        explicit = normalize_thread_id(raw_mailbox)
        if not explicit or not is_houseless_mailbox_thread(explicit):
            raise MailboxThreadResolveError(
                code="mailbox_thread_invalid",
                reason="mailbox_thread is not a parentless mailbox-tagged thread",
            )
        return explicit

    agent = (caller_agent or "").strip()
    if agent:
        matches = _houseless_threads_for_slug(agent)
        if len(matches) > 1:
            raise MailboxThreadResolveError(
                code="mailbox_thread_ambiguous",
                reason=f"caller_agent slug {agent!r} matches multiple mailbox threads",
            )
        if len(matches) == 1:
            return matches[0]
        if ":" in agent:
            tail = agent.split(":")[-1].strip()
            if tail and tail != agent:
                tail_matches = _houseless_threads_for_slug(tail)
                if len(tail_matches) > 1:
                    raise MailboxThreadResolveError(
                        code="mailbox_thread_ambiguous",
                        reason=f"caller_agent slug {tail!r} matches multiple mailbox threads",
                    )
                if len(tail_matches) == 1:
                    return tail_matches[0]

    raw_tid = (thread_id or "").strip()
    fallback = normalize_thread_id(raw_tid) if raw_tid else None
    if fallback and is_houseless_mailbox_thread(fallback):
        return fallback

    raise MailboxThreadResolveError(
        code="mailbox_thread_required",
        reason=(
            "houseless generate requires mailbox_thread or "
            "unambiguous caller_agent mailbox resolve"
        ),
    )
