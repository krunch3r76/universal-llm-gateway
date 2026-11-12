"""Where the routes find the process-wide journal.

The journal is created once per process and reused, because its sqlite file is
the authority and its wake registry is the advisory hint shared by every
long-poll in that process. The lookup prefers a journal already attached to
the running proxy, so a boot sequence that wires one owns the instance and the
routes follow it; absent that, the routes build a default one on the
configured path, which creates its schema on first touch.

A1 ships no boot wiring, so that fallback instance is what every request uses.
It still publishes. The proxy is resolved the way every other ``/api/v1/*``
router resolves it — the app's own ``get_proxy`` dependency, with an override
registered on the app taking precedence — and the ``event_bus`` that proxy owns
is injected into the journal on the first request that reaches the store, so
the ``edge.op.*`` vocabulary reaches the Event Service without a boot step. The
proxy builds that bus in ``init_proxy`` during lifespan startup, which is
before any request arrives; a request that somehow preceded it would publish
nothing and the next one binds. No proxy at all — a test app, or a process that
never initialized one — is not an error here: the routes still answer from the
authority store, with nothing published.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from .store import EdgeOpsJournal

PROXY_ATTRIBUTE = "edge_ops_journal"

_fallback: EdgeOpsJournal | None = None


def journal_for(request: Request) -> EdgeOpsJournal:
    """Return the journal this request should use, with its event bus bound."""
    proxy = _running_proxy(request)
    attached = getattr(proxy, PROXY_ATTRIBUTE, None)
    if isinstance(attached, EdgeOpsJournal):
        return attached
    journal = default_journal()
    journal.bind_event_bus(getattr(proxy, "event_bus", None))
    return journal


def _running_proxy(request: Request) -> Any | None:
    """Resolve the running proxy, or ``None`` when none is initialized.

    Mirrors how the sibling ``/api/v1/*`` routers reach it: the app's
    ``get_proxy`` dependency, with an override registered on the app winning
    so a test can supply its own. The import is deferred to call time to keep
    this package's module graph free of ``systems.proxy``, which pulls in the
    whole proxy implementation.
    """
    from ..proxy.dependencies import get_proxy

    override = request.app.dependency_overrides.get(get_proxy)
    if override is not None:
        return override()
    try:
        return get_proxy()
    except RuntimeError:
        return None


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
