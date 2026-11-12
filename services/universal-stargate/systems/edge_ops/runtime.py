"""Where the routes find the process-wide journal.

The journal is created once per process and reused, because its sqlite file is
the authority and its wake registry is the advisory hint shared by every
long-poll in that process. The lookup prefers a journal already attached to
the running proxy, so a boot sequence that wires one owns the instance and the
routes follow it; absent that, the routes build a default one on the
configured path, which creates its schema on first touch.

A1 ships no boot wiring, because the gate is off and there are no callers yet.
Until a boot step installs a journal here, the fallback instance is what every
request uses and it carries no event bus, so the ``edge.op.*`` vocabulary is
declared and tested but not published at runtime. Wiring that step — construct
``EdgeOpsJournal(event_bus=proxy.event_bus)``, ``await initialize()``, and
install it with ``set_default_journal`` (or attach it as
``proxy.edge_ops_journal``) — is the one thing needed before the flag is
flipped on.
"""

from __future__ import annotations

from fastapi import Request

from .store import EdgeOpsJournal

PROXY_ATTRIBUTE = "edge_ops_journal"

_fallback: EdgeOpsJournal | None = None


def journal_for(request: Request) -> EdgeOpsJournal:
    """Return the journal this request should use."""
    proxy = getattr(request.app.state, "proxy", None)
    attached = getattr(proxy, PROXY_ATTRIBUTE, None)
    if isinstance(attached, EdgeOpsJournal):
        return attached
    return default_journal()


def default_journal() -> EdgeOpsJournal:
    """Process-wide journal on the configured path, created on first use."""
    global _fallback
    if _fallback is None:
        _fallback = EdgeOpsJournal()
    return _fallback


def set_default_journal(journal: EdgeOpsJournal | None) -> None:
    """Install (or clear) the process-wide journal. Tests use this."""
    global _fallback
    _fallback = journal
