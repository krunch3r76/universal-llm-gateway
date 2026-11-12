"""Wire invariants for closeout-memo overflow pointer posts."""

from __future__ import annotations

import hashlib
import re

import pytest

from closeout_memo.models import CloseoutMemoRequest

from . import _transport

pytestmark = pytest.mark.offline


def _memo() -> CloseoutMemoRequest:
    return CloseoutMemoRequest(
        memo_key="giw:lane-9901",
        kind="sdk_closeout",
        status="completed",
        wake_lane="9901",
        worker_thread="9901",
        dispatch_thread="9901",
        dispatch_id="disp-wire",
        execution_id="exec-wire",
        emitted_at="2026-11-12T10:00:00Z",
    )


@pytest.mark.asyncio
async def test_r2_overflow_post_spills_then_cites_cortex_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Posted body cites spill uri; sha256 matches overflow bytes."""
    overflow = "## overflow\n\nundelivered detail\n"
    spill_uri = "cortex://notes/system/threads/9901-auto-overflow-9.md"
    posted: list[dict[str, object]] = []

    def fake_spill(**kwargs: object) -> str:
        assert str(kwargs["overflow"]) == overflow.strip()
        return spill_uri

    async def fake_post(**kwargs: object) -> bool:
        posted.append(dict(kwargs))
        return True

    monkeypatch.setattr(_transport, "spill_overflow_sidecar", fake_spill)
    monkeypatch.setattr(_transport, "post_bus_turn", fake_post)

    ok = await _transport.post_overflow_pointer(
        wake_lane="9901",
        subject="closeout memo — overflow abcd1234",
        memo=_memo(),
        overflow=overflow,
        coalesced_count=1,
    )
    assert ok is True
    assert len(posted) == 1
    body = str(posted[0]["body"])
    assert spill_uri in body
    assert "agent-bus:" not in body
    digest = hashlib.sha256(overflow.strip().encode("utf-8")).hexdigest()
    match = re.search(r"sha256: `([0-9a-f]{64})`", body)
    assert match is not None
    assert match.group(1) == digest
