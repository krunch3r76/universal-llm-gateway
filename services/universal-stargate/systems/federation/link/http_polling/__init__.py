"""
HTTP polling transport for Golem compatibility.

INVARIANT: This module only used when remote.disable_websocket = true
           Fail-fast at factory/constructor, NOT at import.

NOTE: Import-time checks are avoided to support tooling/tests/type-checking.
"""

from __future__ import annotations

from .master.poller import HTTPPollingReceiver
from .remote.state_tracker import TelemetryStateTracker
from .validation import require_polling_mode

__all__ = ["HTTPPollingReceiver", "TelemetryStateTracker", "require_polling_mode"]
