"""Content fingerprint over a projection's *state*, excluding its *age*.

The fingerprint earns its place twice. It suppresses no-op publishes, so a 30 Hz
tick over a quiescent system emits nothing. And it is the determinism falsifier
(Fable F2): replay one fixture twice at the same ``now_ms`` and the hash must
match, or the Model is not pure -- a hidden clock, dict ordering, or set
iteration leaked in.

Both jobs require excluding time-derived scalars. ``generated_at_ms`` and every
``*_age_ms`` field advance on every tick by construction; hashing them would make
the fingerprint change constantly and suppress nothing. The exclusion is
therefore load-bearing, not an optimisation:

* ``generated_at_ms`` -- the tick's own timestamp
* ``fingerprint`` -- cannot hash itself
* ``changed_hints`` -- advisory, per-subscriber, not state
* any key ending ``_age_ms`` -- derived from ``now_ms`` at any nesting depth

Two ingest odometers are excluded for a different reason: ``records_folded`` and
``seq_high_water`` advance on **every** folded record, including records that
change nothing an operator would look at -- telemetry, an unrecognised signal, or
a duplicate re-delivered across a ``resume_from`` overlap. Hashing them would
publish a full snapshot at bus event rate to a View with nothing new to render,
which is precisely the no-op push the fingerprint exists to suppress. They stay in
the frame and stay rendered; they may simply lag one frame behind.

Everything else is in. In particular attention *membership* is hashed, so a
condition crossing a threshold does change the fingerprint and does get
published -- only the churning age counter attached to it does not.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .codec import canonical_json, to_wire
from .dtos import SupervisorProjection

#: Keys excluded from the hash at any nesting depth: tick-local time, the hash
#: field itself, advisory hints, and the two ingest odometers.
EXCLUDED_KEYS = (
    "generated_at_ms",
    "fingerprint",
    "changed_hints",
    "records_folded",
    "seq_high_water",
)

#: Any key with this suffix is excluded at every depth.
EXCLUDED_SUFFIX = "_age_ms"

#: Truncation length. 16 hex chars is 64 bits -- ample for change detection
#: between consecutive frames of one process, and short enough to eyeball in
#: ``--watch`` output. Not a security primitive.
DIGEST_CHARS = 16


def _prune(value: Any) -> Any:
    """Recursively drop excluded keys from JSON-ready ``value``."""
    if isinstance(value, dict):
        return {
            k: _prune(v)
            for k, v in value.items()
            if k not in EXCLUDED_KEYS and not k.endswith(EXCLUDED_SUFFIX)
        }
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def pruned_sections(projection: SupervisorProjection) -> dict[str, Any]:
    """Return ``projection`` as wire data with every excluded key removed.

    Shared with ``changed_hints`` derivation so that "did this section change?" and
    "did the frame change?" are answered against the same pruned view. Without
    that, hints would name ``health`` as changed on every tick purely because an
    age counter advanced.
    """
    pruned = _prune(to_wire(projection))
    return pruned if isinstance(pruned, dict) else {}


def fingerprint_payload(projection: SupervisorProjection) -> str:
    """Return the exact canonical string that :func:`compute` hashes.

    Exposed so a failing determinism test can diff the two payloads and name the
    offending field instead of reporting two opaque hashes.
    """
    return canonical_json(_prune(to_wire(projection)))


def compute(projection: SupervisorProjection) -> str:
    """Return the state fingerprint of ``projection``."""
    payload = fingerprint_payload(projection).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:DIGEST_CHARS]


def compute_from_sections(sections: dict[str, Any]) -> str:
    """Return the fingerprint of an already-pruned section view.

    Lets ``derive`` prune once and use the result for both the hash and the hints
    instead of serialising the frame twice.
    """
    return hashlib.sha256(
        canonical_json(sections).encode("utf-8")
    ).hexdigest()[:DIGEST_CHARS]
