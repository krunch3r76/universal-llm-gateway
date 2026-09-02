"""Explicit hop triplet passthrough from team_dispatch generate to GIW worker POST."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
)


def test_team_dispatch_generate_body_accepts_hop_triplet() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        contract="light-bounded",
        dispatch_thread_id="9964",
        seat="cursor-sdk",
        lane="B",
        hop_from="pred-disp-1",
        hop_seq=2,
        hop_reason="planned",
    )
    assert body.hop_from == "pred-disp-1"
    assert body.hop_seq == 2
    assert body.hop_reason == "planned"


def test_team_dispatch_generate_body_rejects_partial_hop_triplet() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            contract="light-bounded",
            dispatch_thread_id="9964",
            seat="cursor-sdk",
            lane="B",
            hop_seq=2,
        )


@pytest.mark.asyncio
async def test_worker_packet_dispatch_forwards_hop_triplet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def _post(_url: str, *, json: dict[str, object]) -> MagicMock:
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.json.return_value = {"admitted": True}
        return resp

    client = AsyncMock()
    client.post = _post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.worker_base_url",
        lambda: "http://worker.test",
    )

    ok, _detail = await dispatch_cursor_sdk_worker(
        request_id="req-hop",
        thread_id="9964",
        model="composer-2.5",
        execution_id="exec-hop",
        packet_path="tmp/packet.md",
        handoff_contract="light-bounded",
        dispatch_id="disp-hop",
        lane="B",
        hop_from="pred-disp-1",
        hop_seq=2,
        hop_reason="planned",
    )
    assert ok is True
    assert captured[0]["hop_from"] == "pred-disp-1"
    assert captured[0]["hop_seq"] == 2
    assert captured[0]["hop_reason"] == "planned"


@pytest.mark.asyncio
async def test_worker_packet_dispatch_omits_hop_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def _post(_url: str, *, json: dict[str, object]) -> MagicMock:
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.json.return_value = {"admitted": True}
        return resp

    client = AsyncMock()
    client.post = _post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.worker_base_url",
        lambda: "http://worker.test",
    )

    ok, _detail = await dispatch_cursor_sdk_worker(
        request_id="req-no-hop",
        thread_id="9964",
        model="composer-2.5",
        execution_id="exec-no-hop",
        packet_path="tmp/packet.md",
        handoff_contract="light-bounded",
        dispatch_id="disp-no-hop",
        lane="B",
    )
    assert ok is True
    assert "hop_from" not in captured[0]
    assert "hop_seq" not in captured[0]
    assert "hop_reason" not in captured[0]
