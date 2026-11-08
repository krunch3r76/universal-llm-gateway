"""Single source for Orion chatgpt_work paste receipt → ledger / dispatcher outcomes."""

from __future__ import annotations

from typing import Any

from bus_watch.endpoint_wake_composer_defer import is_composer_defer_reason
from bus_watch.orion_wake_bounded_defer import orion_bounded_defer_reason


def map_orion_receipt_to_outcomes(
    receipt: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Return (ledger_outcome, reason_s, extra_wake_detail).

    ``reason_s`` is the top-level ledger/orion-chat-wake reason string
    (matches historical classify_orion).
    """
    extra: dict[str, Any] = {}
    for k in (
        "insert_attempted",
        "inserted",
        "pasted",
        "clicked_unknown",
        "send_fallback_clicked",
        "retryable",
        "receipt",
        "uncertain_side_effect",
        "delivery_unconfirmed",
        "error",
        "code",
        "after_lock",
        "before_lock",
        "phase",
    ):
        if k in receipt and receipt[k] is not None:
            extra[k] = receipt[k]

    top = str(receipt.get("reason") or "").strip()
    ok = receipt.get("ok")
    delivered = receipt.get("delivered")
    pasted = receipt.get("pasted")

    if top == "duplicate_suppressed":
        return "skipped", top, extra

    if ok is True and delivered is True:
        return "delivered", top or "delivered", extra

    if top == "bad_turn":
        return "refused", "bad_turn", extra

    if top == "orion_echo_drop":
        return "skipped", top, extra

    if top == "deferred_streaming":
        return "pending", top, extra

    if top == "deferred_exhausted":
        return "refused", top, extra

    if top == "active_generation":
        return "pending", "deferred_streaming", extra

    if top in {"cdp_unreachable", "conversation_missing"}:
        return "pending", top, extra

    if top in {"approval_unrecognized", "non_connector_dialog"}:
        return "refused", top, extra

    if ok is True and top == "already_inserted":
        return "pending", "already_inserted", extra

    if receipt.get("uncertain_side_effect") is True:
        return (
            "unknown",
            "insert_outcome_unknown",
            {**extra, "reason_code": "insert_outcome_unknown"},
        )

    if receipt.get("send_fallback_clicked") is True:
        return (
            "unknown",
            "insert_outcome_unknown",
            {**extra, "reason_code": "insert_outcome_unknown"},
        )

    if receipt.get("clicked_unknown"):
        rc = top or "send_outcome_unknown"
        return "unknown", rc, {**extra, "reason_code": rc}

    if pasted and not delivered:
        r = str(receipt.get("reason") or "delivery_unconfirmed")
        if r == "cdp_transport_error":
            ins = bool(receipt.get("insert_attempted"))
            phase = str(receipt.get("phase") or "")
            if ins or phase == "post_insert":
                return (
                    "unknown",
                    "insert_outcome_unknown",
                    {**extra, "reason_code": "cdp_transport_error_post_insert"},
                )
        return "unknown", r, {**extra, "reason_code": r}

    if ok is False:
        r = str(receipt.get("reason") or "refused")
        ins_att = bool(receipt.get("insert_attempted"))
        if r == "paste_failed" and not ins_att:
            err = str(receipt.get("error") or "").strip()
            vis = "tab not created by agent has focus or is visible to the operator"
            if vis in err:
                return "refused", vis, {**extra, "reason_code": vis}
            mapped = err or "error"
            return "refused", mapped, {**extra, "reason_code": mapped}
        if r == "cdp_transport_error":
            phase = str(receipt.get("phase") or "")
            if receipt.get("retryable") is True and phase != "post_insert":
                return "pending", r, extra
            return (
                "unknown",
                "insert_outcome_unknown",
                {**extra, "reason_code": "cdp_transport_error_post_insert"},
            )
        if is_composer_defer_reason(r) or orion_bounded_defer_reason(r):
            return "pending", r, {**extra, "composer_defer_reason": r}
        if r in {"insert_outcome_unknown", "delivery_unconfirmed"}:
            return "unknown", r, {**extra, "reason_code": r}
        if r == "send_button_disabled" or (
            receipt.get("insert_attempted") and receipt.get("retryable") is True
        ):
            return "pending", r or "send_button_disabled", extra
        if receipt.get("insert_attempted") and receipt.get("retryable") is False:
            return "unknown", r or "insert_outcome_unknown", {**extra, "reason_code": r}
        return "refused", r, {**extra, "reason_code": r}

    return "unknown", top or "unnamed", {**extra, "reason_code": top or "unnamed"}
