"""U2 address retry when the identity-free followup returns lane_cse_none."""

from __future__ import annotations

from typing import Any

import pytest

from ._transport import followup_body, followup_by_address_body
from .deliver import apply_decision

pytestmark = pytest.mark.offline

_STORED = "https://claude.ai/cowork/cse_stored"
_WAKE_LANE = "999888777"


@pytest.fixture
def memo_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CLOSEOUT_MEMO_LEDGER", str(tmp_path / "memo.sqlite"))


@pytest.fixture
def record_events(
    closeout_memo_event_log: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    return closeout_memo_event_log


def _lane_cse_none() -> dict[str, Any]:
    return {
        "timed_out": False,
        "status_code": 200,
        "body": {"ok": False, "error": "lane_cse_none", "detail": "parked"},
    }


def _attended_404(chat_url: str | None = _STORED) -> dict[str, Any]:
    holder = None
    if chat_url is not None:
        holder = {
            "registration_id": "reg-stored",
            "chat_url": chat_url,
            "evidence_class": "registry_row",
        }
    return {
        "status_code": 404,
        "body": {
            "state": "none",
            "code": "lane_cse_none",
            "seat_holder": holder,
        },
    }


def test_followup_by_address_body_includes_reattach_and_url() -> None:
    base = followup_body(parent_thread="999888777", prompt_text="MEMO")
    body = followup_by_address_body(
        parent_thread="999888777",
        prompt_text="MEMO",
        chat_url=_STORED,
    )
    assert body == {**base, "chat_url": _STORED, "reattach": True}


def test_followup_by_address_body_includes_registration_id_when_set() -> None:
    base = followup_body(parent_thread="999888777", prompt_text="MEMO")
    body = followup_by_address_body(
        parent_thread="999888777",
        prompt_text="MEMO",
        chat_url=_STORED,
        registration_id="reg-stored",
    )
    assert body == {
        **base,
        "chat_url": _STORED,
        "reattach": True,
        "registration_id": "reg-stored",
    }


def test_followup_by_address_body_omits_registration_id_when_empty() -> None:
    body = followup_by_address_body(
        parent_thread="999888777",
        prompt_text="MEMO",
        chat_url=_STORED,
        registration_id="  ",
    )
    assert "registration_id" not in body


@pytest.mark.asyncio
async def test_lane_cse_none_retries_by_stored_address_and_delivers(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return _attended_404(_STORED)

    address_kwargs: dict[str, Any] | None = None

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        nonlocal address_kwargs
        calls.append("followup_by_address")
        address_kwargs = dict(kwargs)
        return {
            "timed_out": False,
            "body": {
                "ok": True,
                "receipt": "dom_committed",
                "url": _STORED,
                "reattach_used": True,
                "lane_created": False,
            },
        }

    async def harvest(**kwargs: Any) -> bool | None:
        pytest.fail("harvest must not run on successful retry")

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended", "followup_by_address"]
    assert address_kwargs is not None
    assert address_kwargs.get("registration_id") == "reg-stored"
    assert outcome["delivered"] is True
    assert outcome["request_had_identity"] is True
    assert outcome["address_retry"] is True
    assert outcome["reattach_used"] is True
    assert outcome["url"] == _STORED
    retry_ev = [p for name, p in record_events if name == "address_retry"]
    assert retry_ev[-1]["outcome"] == "attempted"
    assert retry_ev[-1]["reason"] == "lane_cse_none"
    delivered_ev = [p for name, p in record_events if name == "delivered"]
    assert delivered_ev[-1]["address_retry"] is True
    assert delivered_ev[-1]["reattach_chat_url"] == _STORED
    assert delivered_ev[-1]["reattach_used"] is True
    assert delivered_ev[-1]["lane_created"] is False


@pytest.mark.asyncio
async def test_stored_association_streaming_skips_retry(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return {
            "status_code": 409,
            "body": {
                "state": "ambiguous",
                "code": "lane_cse_ambiguous",
                "reason": "stored_association_streaming",
            },
        }

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {}

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    assert outcome["error"] == "lane_cse_none"
    skip = [p for name, p in record_events if name == "address_retry"][-1]
    assert skip["outcome"] == "skipped"
    assert skip["reason"] == "stored_association_streaming"


@pytest.mark.asyncio
async def test_attended_404_without_seat_holder_skips_retry(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return _attended_404(chat_url=None)

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=_noop_followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    skip = [p for name, p in record_events if name == "address_retry"][-1]
    assert skip["outcome"] == "skipped"
    assert skip["reason"] == "missing_seat_holder"


@pytest.mark.asyncio
async def test_attended_404_empty_chat_url_skips_retry(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return _attended_404("")

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=_noop_followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    skip = [p for name, p in record_events if name == "address_retry"][-1]
    assert skip["reason"] == "empty_chat_url"


@pytest.mark.asyncio
async def test_attended_transport_failure_skips_retry(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return {"status_code": 0, "body": None}

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=_noop_followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    skip = [p for name, p in record_events if name == "address_retry"][-1]
    assert skip["reason"] == "get_transport_failure"


@pytest.mark.asyncio
async def test_attended_503_skips_retry(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return {
            "status_code": 503,
            "body": {"code": "lane_cse_probe_error"},
        }

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=_noop_followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    skip = [p for name, p in record_events if name == "address_retry"][-1]
    assert skip["reason"] == "lane_cse_probe_error"


@pytest.mark.asyncio
async def test_seat_holder_without_registration_id_retries_chat_url_only(
    memo_ledger: None,
) -> None:
    address_kwargs: dict[str, Any] | None = None

    async def followup(**kwargs: Any) -> dict[str, Any]:
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        return {
            "status_code": 404,
            "body": {
                "state": "none",
                "code": "lane_cse_none",
                "seat_holder": {"chat_url": _STORED, "evidence_class": "registry_row"},
            },
        }

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        nonlocal address_kwargs
        address_kwargs = dict(kwargs)
        return {
            "timed_out": False,
            "body": {"ok": True, "receipt": "dom_committed", "url": _STORED},
        }

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert outcome["delivered"] is True
    assert address_kwargs is not None
    assert address_kwargs.get("chat_url") == _STORED
    assert address_kwargs.get("registration_id") is None


@pytest.mark.asyncio
async def test_operator_seat_mismatch_on_retry_falls_back_two_followups_only(
    memo_ledger: None,
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return _attended_404(_STORED)

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {
            "timed_out": False,
            "body": {"ok": False, "error": "operator_seat_mismatch"},
        }

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended", "followup_by_address"]
    assert outcome["needs_fallback"] is True
    assert outcome["error"] == "operator_seat_mismatch"


@pytest.mark.asyncio
async def test_retry_error_class_uses_decision_table_two_followups_only(
    memo_ledger: None,
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {
            "timed_out": False,
            "body": {"ok": False, "error": "lane_busy"},
        }

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=_attended_404_fn,
        followup_by_address=followup_by_address,
    )
    assert calls.count("followup") + calls.count("followup_by_address") == 2
    assert outcome.get("retry") is True
    assert outcome["error"] == "lane_busy:1"


@pytest.mark.asyncio
async def test_retry_timeout_harvests_with_chat_url_no_third_followup(
    memo_ledger: None,
) -> None:
    calls: list[str] = []
    harvest_kwargs: dict[str, Any] | None = None

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {"timed_out": True, "body": None}

    async def harvest(**kwargs: Any) -> bool | None:
        nonlocal harvest_kwargs
        harvest_kwargs = dict(kwargs)
        return False

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=_attended_404_fn,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "followup_by_address"]
    assert harvest_kwargs is not None
    assert harvest_kwargs.get("chat_url") == _STORED
    assert outcome.get("retry") is True


@pytest.mark.asyncio
async def test_attended_200_skips_retry(
    memo_ledger: None,
    record_events: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return {
            "status_code": 200,
            "body": {"state": "current", "current": {"chat_url": "https://live"}},
        }

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
        attended=attended,
        followup_by_address=_noop_followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    skip = [p for name, p in record_events if name == "address_retry"][-1]
    assert skip["reason"] == "get_200_current"


@pytest.mark.asyncio
async def test_without_injection_lane_cse_none_single_followup_only(
    memo_ledger: None,
) -> None:
    """Call count matches pre-U2 injection; error class is lane_cse_none not other."""
    calls = 0

    async def followup(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _lane_cse_none()

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="999888777",
        prompt_text="MEMO",
        followup=followup,
        harvest=_noop_harvest,
    )
    assert calls == 1
    assert outcome["needs_fallback"] is True
    assert outcome["error"] == "lane_cse_none"


async def _noop_harvest(**kwargs: Any) -> bool | None:
    return None


async def _noop_followup_by_address(**kwargs: Any) -> dict[str, Any]:
    pytest.fail("followup_by_address must not run")


async def _attended_404_fn(**kwargs: Any) -> dict[str, Any]:
    return _attended_404(_STORED)
