"""Non-operator registry purpose tags (houseless mailbox rows)."""

from __future__ import annotations

import time
from typing import Any

from claude_bundles import cdp_registry_store as _store
from claude_bundles.what_is_running_view import (
    HOUSELESS_PURPOSES,
    OPERATOR_PURPOSES,
    is_houseless_purpose,
)

from .models import Registration, RegistryError, _row_to_registration, seat_open

# Purposes admissible via ``set_registration_purpose`` (operator seats use other paths).
REGISTRY_TAG_PURPOSES = HOUSELESS_PURPOSES


def require_registry_tag_purpose(purpose: str) -> str:
    """Normalize and validate a non-operator registry tag purpose."""
    purpose_norm = str(purpose or "").strip().lower()
    if not purpose_norm:
        raise RegistryError("purpose is required")
    if purpose_norm in OPERATOR_PURPOSES:
        raise RegistryError(
            f"driving operator purpose must use register_lane/driving_seat; "
            f"got {purpose_norm!r}"
        )
    if purpose_norm not in REGISTRY_TAG_PURPOSES:
        raise RegistryError(
            f"registry tag purpose must be one of {sorted(REGISTRY_TAG_PURPOSES)}; "
            f"got {purpose!r}"
        )
    return purpose_norm


def assert_houseless_row_admissible(row: dict[str, Any]) -> None:
    if str(row.get("parent_thread") or "").strip():
        raise RegistryError("houseless purpose cannot bind parent_thread")
    if seat_open(row):
        raise RegistryError("houseless purpose cannot apply to an open driving seat")


def set_registration_purpose(registration_id: str, purpose: str) -> Registration:
    """Set a non-operator registry tag on an existing host-listable row."""
    rid = (registration_id or "").strip()
    if not rid:
        raise RegistryError("registration_id is required")
    purpose_norm = require_registry_tag_purpose(purpose)
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict):
            raise RegistryError(f"unknown registration_id: {rid!r}")
        status = str(row.get("status") or "")
        if status in ("released", "reclaimed"):
            raise RegistryError(
                f"registration {rid!r} is {status!r}; refusing purpose tag"
            )
        if is_houseless_purpose(purpose_norm):
            assert_houseless_row_admissible(row)
        row = dict(row)
        row["purpose"] = purpose_norm
        row["purpose_tagged_at"] = time.time()
        active[rid] = row
        _store.write_active(active)
        _store.append_log("set_purpose", row)
    return _row_to_registration(row)
