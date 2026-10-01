"""Cancel-safe client-disconnect watching for in-flight HTTP work.

Shared by the pipeline executor (``execution/disconnect_monitor.py``), the
request disconnect guard (``proxy/stargate/requests/disconnect.py``) and the
non-streaming forwarder (``proxy/core/nonstreaming/forwarder.py``). All three
race a unit of work against "the client went away" and must stop the watcher
once the work finishes.

Why polling ``Request.is_disconnected()`` from a monitor task is unsafe:
Starlette implements it as ``receive()`` inside a *pre-cancelled*
``anyio.CancelScope``. When the owner cancels the monitor task while it sits in
that scope, CPython's ``Task.__step`` folds the owner's cancel request into the
anyio cancellation already in flight (``_must_cancel`` is cleared without a
second ``CancelledError``) and anyio's scope exit swallows the one exception it
recognises as its own. The monitor survives its cancellation, and an owner that
then does ``await monitor_task`` blocks until the HTTP response completes.
Observed on ``rag-context``: DAG done at 5 s, ``pipeline.completed`` at exactly
``timeout_seconds`` (executions ``a8077035-905b…``, ``5c7a7716…``), because the
outer ``asyncio.wait_for`` cancellation was then swallowed by the same
``except CancelledError: pass``.

Invariants carried here:
- ``wait_for_client_disconnect`` is a single ``await`` on the ASGI receive
  channel — no polling, no cancel scopes — so ``Task.cancel()`` always ends it.
- ``stop_task`` never swallows a cancellation aimed at the *caller*: it waits via
  ``asyncio.wait``, which does not re-raise the child's ``CancelledError``, and it
  is bounded, so a misbehaving child cannot hold the caller hostage.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from starlette.requests import Request

logger = get_logger(__name__)

# Upper bound on how long an owner waits for a cancelled helper task to unwind.
# A disconnect watcher unwinds in one loop tick; work tasks (DAG execution, an
# upstream HTTP call) may need to release gates and close sockets first.
WATCHER_STOP_GRACE_S = 2.0
WORK_STOP_GRACE_S = 30.0


async def wait_for_client_disconnect(request: Request) -> None:
    """Resolve once the ASGI server reports ``http.disconnect`` for *request*.

    Precondition: the request body has already been consumed (true for every
    JSON endpoint by the time its handler runs). Any residual ``http.request``
    chunks are drained and ignored, matching what ``is_disconnected()`` does.

    Uvicorn also emits ``http.disconnect`` once the response has been fully sent,
    so callers must only race this against work that is still producing the
    response — the pattern every call site follows.
    """
    receive = request.receive
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return


async def stop_task(task: asyncio.Task[Any], *, grace_s: float, what: str) -> bool:
    """Cancel *task* and wait at most *grace_s* for it to reach a terminal state.

    Returns True when the task finished (cancelled, returned or raised) inside
    the grace window. Returns False — after a warning log — when it did not;
    the task is left running and will exit on its own (a disconnect watcher
    resolves when the response completes). The caller's own cancellation is
    never swallowed: ``asyncio.wait`` propagates it untouched.

    A task that raised is drained with ``exception()`` so asyncio does not log
    "Task exception was never retrieved" for a helper we deliberately stopped.
    """
    if not task.done():
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=grace_s)
        if task not in done:
            logger.warning(
                "%s outlived cancellation by %.1fs; leaving it to exit on its own",
                what,
                grace_s,
            )
            return False
    if not task.cancelled():
        _ = task.exception()
    return True


async def stop_tasks(
    tasks: tuple[asyncio.Task[Any], ...], *, grace_s: float, what: str
) -> None:
    """Cancel every unfinished task in *tasks* and wait up to *grace_s* for all of them.

    Used from ``except CancelledError`` handlers, where the owner is itself being
    cancelled and must unwind its helpers without blocking indefinitely and
    without swallowing the cancellation it is about to re-raise. Exceptions from
    finished tasks are drained; the owner re-raises its own ``CancelledError``.
    """
    live = tuple(t for t in tasks if not t.done())
    for task in live:
        task.cancel()
    if live:
        done, pending = await asyncio.wait(set(live), timeout=grace_s)
        for task in pending:
            logger.warning(
                "%s: %s outlived cancellation by %.1fs during unwind",
                what,
                task.get_name(),
                grace_s,
            )
    for task in tasks:
        if task.done() and not task.cancelled():
            _ = task.exception()
