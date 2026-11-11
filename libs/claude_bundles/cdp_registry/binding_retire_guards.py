"""Pre-write guards for dormant binding retire."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_provenance_retire import open_binding_holder_registration
from claude_bundles.cse_url import normalize_cse_url

from .binding_retire_models import refuse_retire
from .binding_retire_resolve import fleet_row, local_row
from .models import STATUS_DORMANT


def status_source_row(
    *,
    registration_id: str | None,
    resolved_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Row used for dormant status — local preferred, else fleet snapshot."""
    rid = (registration_id or "").strip()
    if rid:
        return local_row(rid) or fleet_row(rid) or resolved_row
    return resolved_row


def require_dormant_status(
    row: dict[str, Any] | None,
    *,
    registration_id: str | None,
    chat_url: str | None,
) -> None:
    if row is None:
        return
    status = str(row.get("status") or "")
    if status != STATUS_DORMANT:
        refuse_retire(
            reason="not_dormant",
            message=f"refusing retire: status {status!r} is not dormant",
            registration_id=registration_id,
            chat_url=chat_url,
            status=status or None,
        )


def require_url_holder(
    *,
    registration_id: str,
    chat_url: str,
) -> None:
    """Refuse when the URL's open holder registration differs from ``registration_id``."""
    target = normalize_cse_url(chat_url)
    holder = open_binding_holder_registration(target)
    if holder is not None and holder != registration_id:
        refuse_retire(
            reason="registration_mismatch",
            message="refusing retire: chat_url open holder is a different registration",
            registration_id=registration_id,
            chat_url=target,
            holder_registration_id=holder,
        )


def require_journal_row_when_open(
    *,
    registration_id: str,
    chat_url: str,
    local_row: dict[str, Any] | None,
    resolved_row: dict[str, Any] | None,
) -> None:
    """Refuse retire when journal still shows an open binding but no registry row exists.

    Intentional: satellite authority must hold the ``active.json`` row before mutating.
    """
    if local_row is not None or resolved_row is not None:
        return
    holder = open_binding_holder_registration(chat_url)
    if holder == registration_id:
        refuse_retire(
            reason="journal_open_binding_no_registry_row",
            message=(
                "refusing retire: provenance shows an open binding but no "
                "registry row is present on this host"
            ),
            registration_id=registration_id,
            chat_url=chat_url,
        )


def require_registry_authority(*, will_mutate: bool) -> None:
    if will_mutate and not store.is_seat_authority():
        refuse_retire(
            reason="not_registry_authority",
            message=(
                "refusing retire: this host is not the CDP registry authority; "
                "use cse_session retire_binding on the satellite"
            ),
        )
