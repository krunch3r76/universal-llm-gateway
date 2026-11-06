"""Table-driven parity: map_orion_receipt_to_outcomes vs classify_orion."""

from __future__ import annotations

import pytest
from bus_watch.endpoint_wake_transport import classify_orion
from bus_watch.orion_wake_receipt_map import map_orion_receipt_to_outcomes

pytestmark = pytest.mark.offline

_CASES: list[dict] = [
    {"r": {"ok": False, "reason": "composer_not_empty", "pasted": False}},
    {"r": {"ok": False, "reason": "non_empty_composer", "pasted": False}},
    {"r": {"ok": False, "reason": "composer_selection_active", "pasted": False}},
    {"r": {"ok": False, "reason": "composer_not_focused", "pasted": False}},
    {"r": {"ok": False, "reason": "operator_active", "pasted": False}},
    {"r": {"ok": False, "reason": "conversation_mismatch", "pasted": False}},
    {"r": {"ok": False, "reason": "no_composer", "pasted": False}},
    {
        "r": {
            "ok": False,
            "reason": "cdp_transport_error",
            "retryable": True,
            "pasted": False,
            "phase": "pre_insert",
        }
    },
    {
        "r": {
            "ok": False,
            "reason": "cdp_transport_error",
            "retryable": True,
            "pasted": True,
            "insert_attempted": True,
            "phase": "post_insert",
        }
    },
    {
        "r": {
            "ok": False,
            "reason": "delivery_unconfirmed",
            "pasted": True,
            "delivered": False,
        }
    },
    {"r": {"ok": False, "clicked_unknown": True, "pasted": True, "delivered": False}},
    {
        "r": {
            "ok": False,
            "reason": "send_button_disabled",
            "insert_attempted": True,
            "retryable": True,
            "pasted": False,
        }
    },
    {"r": {"ok": True, "delivered": True, "reason": "delivered"}},
]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: str(c["r"].get("reason", c["r"])))
def test_receipt_map_matches_classify_orion(case: dict) -> None:
    r = case["r"]
    st_m, rs_m, _ = map_orion_receipt_to_outcomes(r)
    st_c, rs_c = classify_orion(r)
    assert (st_m, rs_m) == (st_c, rs_c)
