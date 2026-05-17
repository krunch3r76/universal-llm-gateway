"""
Shared event-record metadata helpers for the universal-stargate monitoring layer.

Deduplicates construction of short event IDs and UTC Z-timestamps that were
previously repeated in every EventLogger logging method. Private module-level
functions only. Callers are the domain-specific private logging mixins in the
sibling modules. Invariants: preserve exact prior string formats; pure functions
with no side effects, no logging, and no I/O.
"""

import uuid
from datetime import datetime


def _new_event_id() -> str:
    """Return an 8-character short identifier derived from UUID4."""
    return str(uuid.uuid4())[:8]


def _utc_timestamp_z() -> str:
    """Return current UTC timestamp in ISO-8601 format with trailing Z."""
    return datetime.utcnow().isoformat() + "Z"
