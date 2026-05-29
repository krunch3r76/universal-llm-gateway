"""Process-level shutdown gate for the manage host.

Tracks in-flight work that must complete before ``./manage`` may exit, so a quit
keystroke (``q`` / ``ctrl+c``) cannot tear down ``manage.sock`` mid-response or
interrupt an operator-initiated fleet/build operation. The failure mode this
closes: an agent calls ``manage(action="sync_restart", ...)``; the operator hits
``q``; the manage process exits; the UDS connection resets mid-handler; the agent
sees a timeout or malformed response and the service is left in a partial state.

Policy is **drain-then-exit, no confirmation dialog** (operator constraint). On
the first quit while busy the gate is flipped to *draining*: new ``manage.sock``
JSON-RPC requests are rejected with a structured retryable error mirroring the
MCP server's restart-drain contract (``services/mcp-server/middleware/drain.py``,
code ``-32099``), while in-flight requests run to completion. Exit then waits —
bounded — for the busy count to reach zero.

Async safety: the manage host is a single Textual asyncio event loop. The UDS
connection handlers, the TUI workers, and the quit-drain loop all run on that one
loop, so plain ``int``/``dict`` mutation between awaits is atomic — no locks
(same discipline ``ManageAPIServer`` already documents).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Bounded ceiling before a forced exit: one ``wait_healthy`` default (120s) plus
# the MCP tool's socket buffer (30s). A handler still in flight past this is
# treated as wedged; exit proceeds with an explicit ``manage.quit.drain.completed``
# event carrying ``timed_out=True`` rather than hanging the TUI forever.
QUIT_DRAIN_TIMEOUT_S = 150.0
RETRY_AFTER_S = 30
SHUTDOWN_ERROR_CODE = -32099  # aligned with MCP drain RESTART_ERROR_CODE
SHUTDOWN_ERROR_REASON = "manage_shutting_down"
SHUTDOWN_ERROR_MESSAGE = "manage host is shutting down; retry in 30s"


@dataclass(slots=True, frozen=True)
class BusySnapshot:
    """Point-in-time view of in-flight work blocking exit."""

    manage_inflight: int
    activities: tuple[str, ...]

    @property
    def busy(self) -> bool:
        return self.manage_inflight > 0 or bool(self.activities)

    @property
    def count(self) -> int:
        return self.manage_inflight + len(self.activities)

    def describe(self) -> str:
        """Human-readable breakdown for the TUI status notification."""
        parts: list[str] = []
        if self.manage_inflight:
            parts.append(f"{self.manage_inflight} manage.sock call(s)")
        parts.extend(self.activities)
        return ", ".join(parts) if parts else "idle"

    def sources(self) -> list[str]:
        """Flat source list for event payloads (manage.sock folded in by count)."""
        sources = list(self.activities)
        if self.manage_inflight:
            sources.append(f"manage.sock:{self.manage_inflight}")
        return sources


class ManageShutdownGate:
    """Tracks manage.sock in-flight JSON-RPC and named long-running TUI activities.

    One instance is owned by ``ServiceController`` so both the manage.sock dispatch
    path (``ManageAPIServer``) and the TUI workers reach the same gate — the same
    shared-chokepoint discipline as ``RestartDrainGate``.

    ``manage_inflight`` is a connection-lifetime counter (covers every dispatched
    JSON-RPC, including non-gated long calls like ``wait_healthy``). ``activities``
    is a ref-counted set of named TUI operations (e.g. ``fleet_deploy``).
    """

    def __init__(self) -> None:
        self._manage_inflight = 0
        self._activities: dict[str, int] = {}
        self._draining = False

    # ── drain state ────────────────────────────────────────────────────────

    def is_draining(self) -> bool:
        """True once a quit-while-busy has flipped the gate; rejects new work."""
        return self._draining

    def begin_drain(self) -> None:
        """Enter drain mode. New manage.sock JSON-RPC is rejected from now on."""
        self._draining = True

    # ── manage.sock in-flight ───────────────────────────────────────────────

    def enter_request(self) -> None:
        """Mark a manage.sock JSON-RPC handler as in flight (call before dispatch)."""
        self._manage_inflight += 1

    def leave_request(self) -> None:
        """Release a manage.sock JSON-RPC handler (call in a finally after dispatch)."""
        if self._manage_inflight > 0:
            self._manage_inflight -= 1

    # ── named TUI activities ────────────────────────────────────────────────

    def set_activity(self, name: str, active: bool) -> None:
        """Ref-count a named long-running TUI activity (fleet deploy, etc.).

        Idempotent per (name, active) pairing via ref counting so overlapping
        registrations of the same activity name cannot prematurely clear it.
        """
        if active:
            self._activities[name] = self._activities.get(name, 0) + 1
            return
        remaining = self._activities.get(name, 0) - 1
        if remaining <= 0:
            self._activities.pop(name, None)
        else:
            self._activities[name] = remaining

    # ── snapshot / rejection ────────────────────────────────────────────────

    def snapshot(self, extra_activities: Iterable[str] = ()) -> BusySnapshot:
        """Capture current busy state.

        ``extra_activities`` folds in busy signals the gate does not own directly
        (e.g. ``ServiceController.build_running``), so callers need not register
        every transient source explicitly.
        """
        activities = (*self._activities.keys(), *extra_activities)
        return BusySnapshot(
            manage_inflight=self._manage_inflight, activities=activities
        )

    def rejection_payload(
        self, req_id: Any, *, retry_after_s: int = RETRY_AFTER_S
    ) -> dict[str, Any]:
        """Canonical JSON-RPC error returned for new requests during quit-drain."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": SHUTDOWN_ERROR_CODE,
                "message": SHUTDOWN_ERROR_MESSAGE,
                "data": {
                    "reason": SHUTDOWN_ERROR_REASON,
                    "retry_after_s": retry_after_s,
                },
            },
        }
