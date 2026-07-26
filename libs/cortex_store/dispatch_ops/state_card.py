"""Work-item state card — durable workflow/stage/bind_status with cached next_action."""

from __future__ import annotations

from typing import Any, Literal

Route = Literal["ADDRESS", "PATH-SIM", "DISPATCH", "held"]


def state_card_defaults() -> dict[str, str]:
    """Return promote-path defaults for a friction-seeded recon-pending todo."""
    return {
        "workflow": "path_sim",
        "stage": "recon",
        "bind_status": "unsettled",
        "next_action": "run_recon_or_path_sim",
    }


def derive_next_action(workflow: str, stage: str, bind_status: str) -> str:
    """Map workflow/stage/bind_status to the v0 next_action verb phrase."""
    if bind_status == "deferred":
        return "await_unblock"
    if workflow == "path_sim" and stage == "recon" and bind_status == "unsettled":
        return "run_recon_or_path_sim"
    if workflow == "path_sim" and bind_status == "settled":
        return "run_address_or_ship"
    if workflow == "address" and stage == "pickup" and bind_status == "settled":
        return "advance_address"
    if workflow == "address" and bind_status == "shipping":
        return "verify_and_close"
    return "inspect_card_and_route"


def merge_state_card(attrs: dict[str, Any]) -> dict[str, Any]:
    """Ensure card keys exist and always refresh ``next_action`` from the trio."""
    merged = dict(attrs)
    defaults = state_card_defaults()
    for key in ("workflow", "stage", "bind_status"):
        raw = merged.get(key)
        if not isinstance(raw, str) or not raw.strip():
            merged[key] = defaults[key]
        else:
            merged[key] = raw.strip()
    merged["next_action"] = derive_next_action(
        str(merged["workflow"]),
        str(merged["stage"]),
        str(merged["bind_status"]),
    )
    return merged


def derive_work_item_route(
    *,
    bind_status: str,
    density_triage: str,
    implement_ready: bool = False,
) -> Route:
    """Total D4 router — one of ADDRESS, PATH-SIM, DISPATCH, or held."""
    triage = (density_triage or "").strip()
    status = (bind_status or "unsettled").strip()

    if status == "deferred":
        return "held"
    if status in {"settled", "shipping"} and triage != "recon_pending":
        return "ADDRESS"
    if status == "unsettled" and triage in {"judgment_required", "recon_pending"}:
        return "PATH-SIM"
    if triage == "mechanical" or (implement_ready and triage != "recon_pending"):
        return "DISPATCH"
    return "PATH-SIM"


__all__ = [
    "derive_next_action",
    "derive_work_item_route",
    "merge_state_card",
    "state_card_defaults",
]
