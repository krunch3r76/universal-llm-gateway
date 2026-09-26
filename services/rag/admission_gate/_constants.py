"""AdmissionGate configuration constants for Event Service and Stargate I/O.

Imported only by ``admission_gate/_io.py``. Re-binds the Event Service Unix
socket and WebSocket subscribe path from ``transport_utils`` and fixes the
Stargate snapshot HTTP timeout and the subscriber reconnect backoff (seconds).
"""

from __future__ import annotations

from transport_utils import EVENTS_QUERY_SOCK, EVENTS_SUBSCRIBE_PATH

_EVENT_QUERY_SOCK = EVENTS_QUERY_SOCK
_SUBSCRIBE_PATH = EVENTS_SUBSCRIBE_PATH
_SNAPSHOT_TIMEOUT_S = 5.0
_RECONNECT_DELAY_S = 5.0
