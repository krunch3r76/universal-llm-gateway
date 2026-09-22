"""N=0 resume identity chain for ``agent_bus.request`` after terminal Auto jobs.

When census ``N==0`` and the snap loaded successfully, bind admission identity
from watch / mailbox-alias / bus CSE / origin CSR before refusing with
``empty_snap`` or ``zero_matches``. Never promote ``superseded_registration_id``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from claude_bundles import hop_seat_cutover

ResumeIdentitySource = Literal[
    "watch_resume",
    "mailbox_resume",
    "cse_resume",
    "origin_cse",
]

_OPERATOR_LANE_RE = re.compile(r"^cdp[-_]operator[-_](\d+)(?:[-_]|$)", re.IGNORECASE)


def home_lane_from_mailbox(from_agent: str | None) -> str | None:
    """Return private-lane id from ``cdp-operator-{id}-*`` mailbox, else ``None``."""
    raw = (from_agent or "").strip()
    if not raw:
        return None
    match = _OPERATOR_LANE_RE.match(raw)
    return match.group(1) if match else None


def _watch_holder_registration(watches: dict[str, dict], thread_key: str) -> str | None:
    """Current watch holder only — never ``superseded_registration_id``."""
    row = watches.get(thread_key)
    if not isinstance(row, dict):
        return None
    reg = str(row.get("registration_id") or "").strip()
    return reg or None


def _resolve_bus_cse_registration(thread_id: str) -> str | None:
    """Last bus CSE association for *thread_id* (fail-soft).

    Reads the host agent-bus socket. Opening messages.db here fails inside
    the MCP container (default path ``/data/messages.db``).
    """
    try:
        import os

        from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
            response = client.get(
                f"/threads/{thread_id}/cse-current",
                headers=headers,
            )
        if response.status_code >= 400:
            return None
        row = response.json()
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    reg = str(row.get("cse_registration_id") or "").strip()
    return reg or None


def resolve_n0_resume_identity(
    *,
    thread_id: str,
    from_agent: str | None,
    path: Path | None = None,
) -> tuple[str | None, ResumeIdentitySource | None]:
    """F1 resume chain when ``census_n == 0`` and snap loaded successfully."""
    tid = (thread_id or "").strip()
    if not tid:
        return None, None

    watches = hop_seat_cutover.load_watches(path)

    reg = _watch_holder_registration(watches, tid)
    if reg:
        return reg, "watch_resume"

    home = home_lane_from_mailbox(from_agent)
    if home and home != tid:
        reg = _watch_holder_registration(watches, home)
        if reg:
            return reg, "mailbox_resume"

    reg = _resolve_bus_cse_registration(tid)
    if reg:
        return reg, "cse_resume"

    from claude_bundles.request_admission_identity import (
        _resolve_origin_cse_registration,
    )

    origin = _resolve_origin_cse_registration(tid)
    if origin:
        return origin, "origin_cse"

    return None, None


__all__ = [
    "ResumeIdentitySource",
    "home_lane_from_mailbox",
    "resolve_n0_resume_identity",
]
