"""Pre-pour harvest gate and ordering for cursor resume fence delivery."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from agent_bus_store.resume_fence_delivery import _pour_or_adopt

pytestmark = pytest.mark.offline

_ANCHOR = "550e8400-e29b-41d4-a716-446655440099"
_TID = str(uuid.uuid4())


def _assemble_side_effect(*_args, **kwargs):
    return {
        "fence": {"fence_id": "f1"},
        "pre_pour_harvest": kwargs.get("pre_pour_harvest"),
    }


@pytest.mark.parametrize(
    ("surface", "transcript_id", "expected_reason"),
    [
        ("claude_ai", str(uuid.uuid4()), "surface_claude_ai"),
        ("claude_ai", None, "surface_claude_ai"),
        ("cursor", None, "no_transcript_id"),
        ("cursor", "", "no_transcript_id"),
        ("cursor", "tab-x", "transcript_id_not_uuid"),
        (None, str(uuid.uuid4()), "surface_unspecified"),
    ],
)
def test_resume_harvest_refused_without_cursor_transcript(
    surface: str | None,
    transcript_id: str | None,
    expected_reason: str,
) -> None:
    with (
        patch(
            "agent_bus_store.tape_harvest._call_transcript_harvest",
            side_effect=AssertionError("harvest must not run"),
        ),
        patch(
            "agent_bus_store.tape_harvest.explicit_uuids_for_lane",
            side_effect=AssertionError("lane must not be consulted"),
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.read_stored_bundle",
            return_value=None,
        ),
        patch(
            "agent_bus_store.resume_fence.assemble_resume_fence",
            side_effect=_assemble_side_effect,
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.assemble_resume_fence",
            side_effect=_assemble_side_effect,
        ),
        patch("agent_bus_store.resume_fence_delivery.store_bundle"),
        patch(
            "agent_bus_store.resume_fence_delivery._stamp_live_state",
            side_effect=lambda b, _f: b,
        ),
        patch(
            "agent_bus_store.events.resume_fence.emit_resume_fence_harvest_decided",
        ),
    ):
        bundle = _pour_or_adopt(
            "10223",
            transcript_id=transcript_id,
            source="test",
            pool=None,
            surface=surface,
        )
    assert "error" not in bundle or not bundle.get("error")
    record = bundle["pre_pour_harvest"]
    assert record["outcome"] == "skipped"
    assert record["reason"] == expected_reason


def test_pre_pour_harvest_order_and_shape() -> None:
    call_order: list[str] = []

    def _harvest(*_a, **_k):
        call_order.append("harvest")
        return {"discovered": 1, "sealed": 1, "deferred_count": 0, "refused": 0, "quiescent": 0}

    def _assemble(*_a, **kw):
        call_order.append("assemble")
        return {
            "fence": {"fence_id": "f1"},
            "pre_pour_harvest": kw.get("pre_pour_harvest"),
        }

    with (
        patch(
            "agent_bus_store.tape_harvest.explicit_uuids_for_lane",
            return_value={_ANCHOR},
        ),
        patch(
            "agent_bus_store.tape_harvest._call_transcript_harvest",
            side_effect=_harvest,
        ) as mock_harvest,
        patch(
            "agent_bus_store.resume_fence_delivery.read_stored_bundle",
            return_value=None,
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.assemble_resume_fence",
            side_effect=_assemble,
        ),
        patch("agent_bus_store.resume_fence_delivery.store_bundle"),
        patch(
            "agent_bus_store.resume_fence_delivery._stamp_live_state",
            side_effect=lambda b, _f: b,
        ),
        patch(
            "agent_bus_store.events.resume_fence.emit_resume_fence_harvest_decided",
        ),
    ):
        _pour_or_adopt(
            "10223",
            transcript_id=_TID,
            source="test",
            pool=None,
            surface="cursor",
        )

    assert call_order == ["harvest", "assemble"]
    mock_harvest.assert_called_once_with(
        thread_id="10223",
        explicit_ids=[_ANCHOR],
        max_seals=8,
        timeout=8.0,
    )


def test_empty_lane_anchors_skips_harvest() -> None:
    with (
        patch(
            "agent_bus_store.tape_harvest.explicit_uuids_for_lane",
            return_value=set(),
        ),
        patch(
            "agent_bus_store.tape_harvest._call_transcript_harvest",
            side_effect=AssertionError("harvest must not run"),
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.read_stored_bundle",
            return_value=None,
        ),
        patch(
            "agent_bus_store.resume_fence.assemble_resume_fence",
            side_effect=_assemble_side_effect,
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.assemble_resume_fence",
            side_effect=_assemble_side_effect,
        ),
        patch("agent_bus_store.resume_fence_delivery.store_bundle"),
        patch(
            "agent_bus_store.resume_fence_delivery._stamp_live_state",
            side_effect=lambda b, _f: b,
        ),
        patch(
            "agent_bus_store.events.resume_fence.emit_resume_fence_harvest_decided",
        ),
    ):
        bundle = _pour_or_adopt(
            "10223",
            transcript_id=str(uuid.uuid4()),
            source="test",
            pool=None,
            surface="cursor",
        )
    record = bundle["pre_pour_harvest"]
    assert record["outcome"] == "skipped"
    assert record["reason"] == "no_lane_anchors"


@pytest.mark.parametrize(
    "exc_factory,reason",
    [
        (lambda: httpx.TimeoutException("t"), "harvest_timeout"),
        (lambda: httpx.HTTPError("x"), "harvest_unreachable"),
    ],
)
def test_harvest_failure_still_pours(exc_factory, reason: str) -> None:
    with (
        patch(
            "agent_bus_store.tape_harvest.explicit_uuids_for_lane",
            return_value={_ANCHOR},
        ),
        patch(
            "agent_bus_store.tape_harvest._call_transcript_harvest",
            return_value={
                "error": "failed",
                "reason": reason,
            },
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.read_stored_bundle",
            return_value=None,
        ),
        patch(
            "agent_bus_store.resume_fence.assemble_resume_fence",
            side_effect=_assemble_side_effect,
        ),
        patch(
            "agent_bus_store.resume_fence_delivery.assemble_resume_fence",
            side_effect=_assemble_side_effect,
        ),
        patch("agent_bus_store.resume_fence_delivery.store_bundle"),
        patch(
            "agent_bus_store.resume_fence_delivery._stamp_live_state",
            side_effect=lambda b, _f: b,
        ),
        patch(
            "agent_bus_store.events.resume_fence.emit_resume_fence_harvest_decided",
        ),
    ):
        bundle = _pour_or_adopt(
            "10223",
            transcript_id=str(uuid.uuid4()),
            source="test",
            pool=None,
            surface="cursor",
        )
    record = bundle["pre_pour_harvest"]
    assert record["outcome"] == "failed"
    assert record["reason"] == reason
    assert "fence" in bundle
