"""Delivery outcome dataclass — return-value contract from ``deliver_result``.

Defined in its own module so the tracker (``async_tracker.py``) can lazy-import
``DeliveryOutcome`` without dragging in the HTTP/event machinery of the rest
of the delivery package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class DeliveryOutcome:
    """Return value from ``deliver_result`` — allows the tracker to act on failures.

    ``status`` is one of:
    - ``"delivered"``: success (envelope posted or on-behalf reply landed).
    - ``"failed"``: delivery attempt failed (see ``failure_reason``).
    - ``"skipped"``: no delivery config present; no attempt was made.

    The tracker's ``_run_delivery_with_outcome()`` consults this to demote
    a ``op="to_thread"`` record from ``completed`` to ``failed`` on any
    non-delivered outcome.
    """

    status: Literal["delivered", "failed", "skipped"]
    failure_reason: str | None = None
