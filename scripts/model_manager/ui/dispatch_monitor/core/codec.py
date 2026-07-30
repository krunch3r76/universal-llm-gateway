"""ProjectionCodec -- the wire *schema* for the projection channel. No socket.

The core owns the frame shape; the G5 graft owns the carrier. ``libs/projection``
hosted by the Controller moves these frames over UDS by default, TCP/WS by
configuration. Nothing in this module opens, binds, connects or blocks.

Two frame kinds:

``handshake``
    Sent once, on connect, ahead of the first snapshot. Carries
    ``schema_version`` and the ``command_endpoint`` hint, so the View has **one
    discovery point and two carriers** and hardcodes no address (Fable §3.2). It
    does not carry state.

``snapshot``
    A whole :class:`~dispatch_monitor_core.dtos.SupervisorProjection`. v1 is
    snapshot-only: deltas are an unmeasured optimisation at operator scale and are
    gated behind falsifier F-delta.

Encoding is canonical JSON -- sorted keys, tight separators, ASCII-escaped -- so
that byte equality of two frames means state equality of two projections. The
fingerprint depends on that property.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from .dtos import (
    SCHEMA_VERSION,
    AttentionItem,
    CdpLegRow,
    CharterRootRow,
    HealthProjection,
    PathSimArcRow,
    SdkDispatchRow,
    SupervisorProjection,
)

FRAME_HANDSHAKE = "handshake"
FRAME_SNAPSHOT = "snapshot"

#: Which concrete row type sits behind each collection field of the projection.
_TUPLE_FIELD_TYPES = {
    "roots": CharterRootRow,
    "sdk": SdkDispatchRow,
    "cdp": CdpLegRow,
    "attention": AttentionItem,
}


def _encode(value: Any) -> Any:
    """Recursively convert dataclasses, tuples and mappings to JSON-ready data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def to_wire(projection: SupervisorProjection) -> dict[str, Any]:
    """Return ``projection`` as a plain JSON-ready dict."""
    return _encode(projection)


def _tuple_fields(row_type: type) -> frozenset[str]:
    """Return the names of ``row_type`` fields declared as tuples.

    JSON has one sequence type, so a tuple field decodes to a list and the
    rebuilt row would compare unequal to the original. Coercing back is what makes
    ``decode(encode(p)) == p`` hold, which the round-trip test pins.
    """
    return frozenset(f.name for f in fields(row_type) if "tuple" in str(f.type))


def _row(row_type: type, data: Mapping[str, Any]) -> Any:
    """Build ``row_type`` from ``data``, ignoring unknown keys.

    Unknown fields are dropped rather than raising: a decoder built against
    schema 1.0 must survive a 1.1 producer that added an optional field. That is
    the receiver half of the additive-only versioning policy.
    """
    known = {f.name for f in fields(row_type)}
    as_tuple = _tuple_fields(row_type)
    kwargs = {}
    for key, value in data.items():
        if key not in known:
            continue
        kwargs[key] = tuple(value) if key in as_tuple and isinstance(value, list) else value
    return row_type(**kwargs)


def from_wire(data: Mapping[str, Any]) -> SupervisorProjection:
    """Rebuild a :class:`SupervisorProjection` from decoded wire data.

    Raises ``ValueError`` when the producer's major schema exceeds this build's.
    Refusing is deliberate -- silently rendering a newer schema on partial field
    knowledge is the worse outcome (Fable §3.3).
    """
    version = int(data.get("schema_version", SCHEMA_VERSION))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"projection schema_version {version} exceeds supported {SCHEMA_VERSION}"
        )
    kwargs: dict[str, Any] = {
        "schema_version": version,
        "generated_at_ms": int(data.get("generated_at_ms", 0)),
        "fingerprint": str(data.get("fingerprint", "")),
        "changed_hints": tuple(data.get("changed_hints", ()) or ()),
    }
    kwargs["health"] = _row(HealthProjection, data.get("health", {}) or {})
    for name, row_type in _TUPLE_FIELD_TYPES.items():
        kwargs[name] = tuple(_row(row_type, r) for r in data.get(name, ()) or ())
    kwargs["arcs"] = {
        str(k): _row(PathSimArcRow, v) for k, v in (data.get("arcs") or {}).items()
    }
    return SupervisorProjection(**kwargs)


def canonical_json(data: Any) -> str:
    """Serialise ``data`` so that equal state always yields equal bytes."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class ProjectionCodec:
    """Frame encoder/decoder for the projection channel.

    Stateless and I/O-free by construction -- it is handed strings and returns
    strings. The Controller is the only component that knows a socket exists.
    """

    @staticmethod
    def encode_handshake(command_endpoint: str | None = None) -> str:
        """Return the connect-time handshake frame as one canonical JSON line."""
        return canonical_json(
            {
                "kind": FRAME_HANDSHAKE,
                "schema_version": SCHEMA_VERSION,
                "command_endpoint": command_endpoint,
                "snapshot_only": True,
            }
        )

    @staticmethod
    def encode_snapshot(projection: SupervisorProjection) -> str:
        """Return ``projection`` as one canonical JSON snapshot frame."""
        return canonical_json({"kind": FRAME_SNAPSHOT, "projection": to_wire(projection)})

    @staticmethod
    def decode_frame(text: str) -> tuple[str, Any]:
        """Decode one frame line into ``(kind, payload)``.

        ``payload`` is a :class:`SupervisorProjection` for snapshot frames and the
        raw dict for handshakes. Unknown frame kinds decode to their raw dict so a
        future kind is ignorable rather than fatal.
        """
        data = json.loads(text)
        kind = str(data.get("kind", ""))
        if kind == FRAME_SNAPSHOT:
            return kind, from_wire(data.get("projection", {}))
        return kind, data
