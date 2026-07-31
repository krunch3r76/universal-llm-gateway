"""Friction 23301 — server pointer turns must never become dispatch prompts.

Single-thread Q/R reuse posts the generate pointer (and failure turns) onto
the dispatch thread itself; before this guard a subsequent dispatch against
the same thread consumed the server turn as the model prompt verbatim
(threads 4741/4744 — cascading pointer prompts, models reviewing the pointer
envelope instead of the caller brief).
"""

from __future__ import annotations

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.dispatch_thread_context import (
    is_server_dispatch_turn_body,
)
from systems.frontier_consult.handoff import build_generate_dispatch_pointer

pytestmark = pytest.mark.offline


def test_api_generate_pointer_body_detected() -> None:
    body = build_generate_dispatch_pointer(
        lane="skeptic",
        contract="light-bounded",
        dispatch_thread_id="4741",
        correlation_id="ca68430154ba",
        summary="CHALLENGE — blind panel",
    )
    assert is_server_dispatch_turn_body(body)


def test_sdk_packet_pointer_body_detected() -> None:
    assert is_server_dispatch_turn_body(
        "SDK implement dispatch — see packet `tmp/reviews/x.md`."
    )


def test_failure_turn_body_detected() -> None:
    assert is_server_dispatch_turn_body(
        "Automated skeptic generate dispatch failed (admission error or "
        "non-JSON forward). Re-dispatch manually or inspect the dispatch error."
    )


def test_caller_prompt_not_detected() -> None:
    assert not is_server_dispatch_turn_body(
        "CHALLENGE — blind panel (do not assume other reviewers exist).\n\n"
        "DECISION: A licensed PharmD is deciding whether to pursue a PIC role."
    )
    assert not is_server_dispatch_turn_body("")


@pytest.mark.asyncio
async def test_read_latest_rejects_pointer_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a pointer-latest thread rejects 422 before dispatch."""
    from systems.frontier_consult import dispatch_thread_context as dtc

    pointer = build_generate_dispatch_pointer(
        lane="reviewer",
        contract="light-bounded",
        dispatch_thread_id="4741",
        correlation_id="94af4263019d",
        summary="skeptic light-bounded generate dispatch",
    )

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"turns": [{"body": pointer}]}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, *_a: object, **_kw: object) -> _Resp:
            return _Resp()

    monkeypatch.setattr(dtc, "make_async_client", lambda *a, **kw: _Client())

    with pytest.raises(FrontierEndpointError) as excinfo:
        await dtc.read_latest_dispatch_thread_body(
            request_id="req-guard", dispatch_thread_id="4741"
        )
    assert excinfo.value.code == "dispatch_thread_latest_is_pointer"