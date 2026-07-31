"""Unit tests for POST /api/v1/implement/closeout idempotent, validated ingress.

Covers the C-prime ingress hardening (todo:wire-closeout-trigger-consumer):
in-memory TTL dedupe, status presence, deploy-state gate, and source_ref
resolvability on the keyed (producer) path vs fail-closed manual path.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from . import route as route_mod
from .route import ImplementCloseoutBody, implement_closeout


@pytest.fixture(autouse=True)
def _clear_dedupe() -> Iterator[None]:
    route_mod._closeout_dedupe.clear()
    yield
    route_mod._closeout_dedupe.clear()


@pytest.fixture
def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _stub(closeout: dict[str, Any], *, source_ref: str | None = None) -> dict:
        calls.append({"closeout": closeout, "source_ref": source_ref})
        return {"ok": True}

    monkeypatch.setattr(route_mod, "run_implement_closeout_pipeline", _stub)
    return calls


def _closeout(**overrides: Any) -> dict[str, Any]:
    base = {"schema_version": 1, "status": "complete", "summary": "s"}
    base.update(overrides)
    return base


def _status_and_body(result: Any) -> tuple[int, dict[str, Any]]:
    if hasattr(result, "status_code"):
        return result.status_code, json.loads(result.body)
    return 200, result


@pytest.mark.asyncio
async def test_dedupe_second_call_skips_pipeline(
    _stub_pipeline: list[dict[str, Any]],
) -> None:
    body = ImplementCloseoutBody(
        closeout=_closeout(files_created=["docs/readme.md"]),
        source_ref="todo:x",
        idempotency_key="k1",
    )
    first = await implement_closeout(body)
    assert _status_and_body(first)[1].get("ok") is True
    assert len(_stub_pipeline) == 1

    second = await implement_closeout(
        ImplementCloseoutBody(
            closeout=_closeout(), source_ref="todo:x", idempotency_key="k1"
        )
    )
    status, payload = _status_and_body(second)
    assert status == 200
    assert payload["deduped"] is True
    assert len(_stub_pipeline) == 1


@pytest.mark.asyncio
async def test_unresolvable_source_ref_422(
    _stub_pipeline: list[dict[str, Any]],
) -> None:
    body = ImplementCloseoutBody(
        closeout=_closeout(),
        source_ref="workspaces://a/b.md",
        idempotency_key="k2",
    )
    status, payload = _status_and_body(await implement_closeout(body))
    assert status == 422
    assert payload["error"]["code"] == "closeout_source_unresolvable"
    assert len(_stub_pipeline) == 0


@pytest.mark.asyncio
async def test_missing_status_422(_stub_pipeline: list[dict[str, Any]]) -> None:
    closeout = _closeout()
    del closeout["status"]
    body = ImplementCloseoutBody(closeout=closeout, source_ref="todo:x")
    status, payload = _status_and_body(await implement_closeout(body))
    assert status == 422
    assert payload["error"]["code"] == "closeout_invalid"
    assert len(_stub_pipeline) == 0


@pytest.mark.asyncio
async def test_manual_path_no_key_requires_resolvable_ref(
    _stub_pipeline: list[dict[str, Any]],
) -> None:
    body = ImplementCloseoutBody(closeout=_closeout(), source_ref="workspaces://a/b.md")
    status, payload = _status_and_body(await implement_closeout(body))
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"
    assert "recovery:" in payload["error"]["message"]
    assert len(_stub_pipeline) == 0
