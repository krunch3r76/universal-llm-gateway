"""Variable-id dispatch resolves the canonical URL before POST."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.rag import knowledge_extractor

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_submit_posts_url_from_capability_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict]] = []
    canonical = "/api/v1/capabilities/extract/rag-extraction"

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    async def _get(url: str, **kwargs: object) -> _Response:
        calls.append(("get", url, kwargs))
        return _Response({"url": canonical})

    async def _post(url: str, **kwargs: object) -> _Response:
        calls.append(("post", url, kwargs))
        return _Response({"execution_id": "exec-1"})

    monkeypatch.setattr(
        knowledge_extractor,
        "_client",
        SimpleNamespace(get=_get, post=_post),
    )
    config = SimpleNamespace(pipeline="rag-extraction")
    execution_id = await knowledge_extractor.submit_extraction_pipeline(
        ["c1"],
        ["text"],
        config,  # type: ignore[arg-type]
    )
    assert execution_id == "exec-1"
    assert calls[0][0] == "get"
    assert calls[0][1].endswith("/api/v1/capabilities")
    assert calls[0][2]["params"] == {"id": "rag-extraction"}
    assert calls[1][0] == "post"
    assert calls[1][1].endswith(canonical)
    assert calls[1][2]["json"]["model"] == "rag-extraction"
