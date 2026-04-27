"""AdmissionGate configuration constants."""

from __future__ import annotations

import os

_EVENT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"  # host ignored with UDS
_SNAPSHOT_TIMEOUT_S = 5.0
_RECONNECT_DELAY_S = 5.0
