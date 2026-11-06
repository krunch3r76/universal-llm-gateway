"""Distinct identity types for concern aggregate references (A12)."""

from __future__ import annotations

from typing import NewType

RecordRef = NewType("RecordRef", str)
IntentId = NewType("IntentId", str)
RequestId = NewType("RequestId", str)
DeliveryId = NewType("DeliveryId", str)
CursorToken = NewType("CursorToken", str)
WriteIntentId = NewType("WriteIntentId", str)

_ID_PREFIXES: dict[type, str] = {
    RecordRef: "rec",
    IntentId: "int",
    RequestId: "req",
    DeliveryId: "del",
    CursorToken: "cur",
    WriteIntentId: "wi",
}


class IdentityTypeError(TypeError):
    """Raised when an identity value is used with the wrong type or shape."""


def _validate_shape(value: str, *, expected: type, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityTypeError(f"{label} must be a non-empty string")
    prefix = _ID_PREFIXES.get(expected)
    if prefix and not value.startswith(f"{prefix}:"):
        raise IdentityTypeError(
            f"{label} must use prefix {prefix!r}, got {value!r}"
        )
    return value.strip()


def as_record_ref(value: str) -> RecordRef:
    """Coerce *value* to ``RecordRef``; rejects other identity prefixes."""
    _reject_cross_prefix(value, allowed="")
    if not isinstance(value, str) or not value.strip():
        raise IdentityTypeError("record_ref must be a non-empty string")
    return RecordRef(value.strip())


def as_intent_id(value: str) -> IntentId:
    _reject_cross_prefix(value, allowed=_ID_PREFIXES[IntentId])
    return IntentId(_validate_shape(value, expected=IntentId, label="intent_id"))


def as_request_id(value: str) -> RequestId:
    _reject_cross_prefix(value, allowed=_ID_PREFIXES[RequestId])
    return RequestId(_validate_shape(value, expected=RequestId, label="request_id"))


def as_delivery_id(value: str) -> DeliveryId:
    _reject_cross_prefix(value, allowed=_ID_PREFIXES[DeliveryId])
    return DeliveryId(
        _validate_shape(value, expected=DeliveryId, label="delivery_id")
    )


def as_cursor_token(value: str) -> CursorToken:
    _reject_cross_prefix(value, allowed=_ID_PREFIXES[CursorToken])
    return CursorToken(
        _validate_shape(value, expected=CursorToken, label="cursor")
    )


def as_write_intent_id(value: str) -> WriteIntentId:
    _reject_cross_prefix(value, allowed=_ID_PREFIXES[WriteIntentId])
    return WriteIntentId(
        _validate_shape(value, expected=WriteIntentId, label="write_intent_id")
    )


def _reject_cross_prefix(value: str, *, allowed: str) -> None:
    for prefix in _ID_PREFIXES.values():
        if prefix == allowed:
            continue
        if value.startswith(f"{prefix}:"):
            raise IdentityTypeError(
                f"identity {value!r} uses prefix {prefix!r}, expected {allowed!r}"
            )


def parse_transition_identities(
    *,
    record_ref: str,
    intent_id: str | None = None,
    request_id: str | None = None,
    delivery_id: str | None = None,
) -> tuple[RecordRef, IntentId | None, RequestId | None, DeliveryId | None]:
    """Validate transition identity fields; distinct columns, no coercion."""
    rec = as_record_ref(record_ref)
    iid = as_intent_id(intent_id) if intent_id is not None else None
    rid = as_request_id(request_id) if request_id is not None else None
    did = as_delivery_id(delivery_id) if delivery_id is not None else None
    return rec, iid, rid, did
