"""U2 address retry when the identity-free followup returns lane_cse_none."""

from __future__ import annotations

from typing import Any

import pytest

from .deliver import apply_decision

pytestmark = pytest.mark.offline

_STORED = "https://claude.ai/cowork/cse_stored"


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


@pytest.mark.asyncio
async def test_lane_cse_none_retries_by_stored_address_and_delivers() -> None:
    calls: list[str] = []
    retry_body: dict[str, Any] | None = None

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return _attended_404(_STORED)

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        nonlocal retry_body
        retry_body = dict(kwargs)
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
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended", "followup_by_address"]
    assert retry_body == {
        "parent_thread": "12286",
        "prompt_text": "MEMO",
        "chat_url": _STORED,
    }
    assert outcome["delivered"] is True
    assert outcome["request_had_identity"] is True
    assert outcome["address_retry"] is True
    assert outcome["reattach_used"] is True


@pytest.mark.asyncio
async def test_stored_association_streaming_skips_retry() -> None:
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

    async def harvest(**kwargs: Any) -> bool | None:
        return None

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True
    assert outcome["error"] == "lane_cse_none"


@pytest.mark.asyncio
async def test_attended_404_without_seat_holder_skips_retry() -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return _attended_404(chat_url=None)

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {}

    async def harvest(**kwargs: Any) -> bool | None:
        return None

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True


@pytest.mark.asyncio
async def test_retry_error_class_uses_decision_table_two_followups_only() -> None:
    calls: list[str] = []

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        return _attended_404(_STORED)

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {
            "timed_out": False,
            "body": {"ok": False, "error": "lane_busy"},
        }

    async def harvest(**kwargs: Any) -> bool | None:
        return None

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls.count("followup") + calls.count("followup_by_address") == 2
    assert outcome.get("retry") is True
    assert outcome["error"] == "lane_busy:1"


@pytest.mark.asyncio
async def test_retry_timeout_harvests_with_chat_url_no_third_followup() -> None:
    calls: list[str] = []
    harvest_kwargs: dict[str, Any] | None = None

    async def followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        return _attended_404(_STORED)

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {"timed_out": True, "body": None}

    async def harvest(**kwargs: Any) -> bool | None:
        nonlocal harvest_kwargs
        harvest_kwargs = dict(kwargs)
        return False

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "followup_by_address"]
    assert harvest_kwargs is not None
    assert harvest_kwargs.get("chat_url") == _STORED
    assert outcome.get("retry") is True


@pytest.mark.asyncio
async def test_attended_200_skips_retry() -> None:
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

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {}

    async def harvest(**kwargs: Any) -> bool | None:
        return None

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert calls == ["followup", "attended"]
    assert outcome["needs_fallback"] is True


@pytest.mark.asyncio
async def test_without_injection_lane_cse_none_matches_prior_call_count() -> None:
    calls = 0

    async def followup(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _lane_cse_none()

    async def harvest(**kwargs: Any) -> bool | None:
        return None

    outcome = await apply_decision(
        memo_ids=["m1"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
    )
    assert calls == 1
    assert outcome["needs_fallback"] is True
    assert outcome["error"] == "lane_cse_none"
