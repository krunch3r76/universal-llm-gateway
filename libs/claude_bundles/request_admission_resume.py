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


def _watch_holder_registration(
    watches: dict[str, dict], thread_key: str
) -> str | None:
    """Current watch holder only — never ``superseded_registration_id``."""
    row = watches.get(thread_key)
    if not isinstance(row, dict):
        return None
    reg = str(row.get("registration_id") or "").strip()
    return reg or None


def _resolve_origin_cse_registration(thread_id: str) -> str | None:
    """Delegate to identity module (lazy — avoids import cycle at load)."""
    from claude_bundles.request_admission_identity import (
        _resolve_origin_cse_registration as _identity_origin,
    )

    return _identity_origin(thread_id)


def _resolve_bus_cse_registration(thread_id: str) -> str | None:
    """Last bus CSE association for *thread_id* (fail-soft)."""
    try:
        from agent_bus_store.db.cse_associations import get_current_cse

        row = get_current_cse(thread_id=thread_id)
    except (ImportError, LookupError, OSError, ValueError):
        return None
    except Exception:
        # Hermetic tests and offline seats may lack agent_bus_store DB.
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

    origin = _resolve_origin_cse_registration(tid)
    if origin:
        return origin, "origin_cse"

    return None, None


__all__ = [
    "ResumeIdentitySource",
    "home_lane_from_mailbox",
    "resolve_n0_resume_identity",
]
