"""Storage-readiness refusal for registry mint and hygiene paths (V1 draft, stage-1b text)."""

from __future__ import annotations

import contextlib
import os
import socket

from browser_governance import events as _events
from browser_governance.contract import storage_ready


def storage_refused(caller: str) -> bool:
    """Return True, after emitting ``browser.storage.unavailable``, when the profile volume is not ready.

    The sentinel is read on every call and nothing is cached, so a volume that
    goes away between two calls is seen by the second. The event is advisory;
    the caller's refusal is the control path and does not depend on delivery.
    """
    if storage_ready():
        return False
    with contextlib.suppress(Exception):
        _events.emit_browser_event(
            _events.browser_storage_unavailable(
                sentinel=os.environ.get("ULG_BROWSER_STORAGE_SENTINEL", ""),
                host=socket.gethostname(),
                caller=caller,
            ),
            source="cdp-registry",
        )
    return True
