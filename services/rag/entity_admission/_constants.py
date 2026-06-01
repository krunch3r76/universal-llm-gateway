"""EntityAdmissionGate configuration constants."""

from __future__ import annotations

from transport_utils import EVENTS_QUERY_SOCKET_PATH, EVENTS_SUBSCRIBE_PATH

_EVENT_QUERY_SOCK = EVENTS_QUERY_SOCKET_PATH
_SUBSCRIBE_PATH = EVENTS_SUBSCRIBE_PATH

# cortex-api endpoint returning resolved absolute paths of all entities
# carrying a source_uri (Task 8). RAG reaches cortex-api over UDS via
# transport_utils.make_async_client(DEFAULT_CORTEX_URL) — never cortex.db.
_SOURCE_PATHS_ENDPOINT = "/api/v1/entities/source-paths"

_SNAPSHOT_TIMEOUT_S = 10.0
# Steady-state backstop refresh interval (self-heals a missed event).
_BACKSTOP_INTERVAL_S = 300.0
# Faster retry while the gate has never successfully loaded (cortex-api not
# yet up at RAG startup). Caps the fail-closed hold window for legitimate
# backed files to this interval rather than the full backstop interval.
_UNREADY_RETRY_S = 10.0
# Debounce window: coalesce a burst of cortex.entity.source.changed events
# into a single full re-fetch.
_REFRESH_DEBOUNCE_S = 2.0
_RECONNECT_DELAY_S = 5.0
