"""Executor-level OpenAI reviewer path (review_openai step)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_PKG = "writing_v1_handlers"
_V1 = Path(__file__).resolve().parents[1]
if _PKG not in sys.modules:
    _HANDLERS = _V1 / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

seat_dispatch = importlib.import_module("writing_v1_handlers.seat_dispatch")
seat_wait = importlib.import_module("writing_v1_handlers.seat_wait")
finalize = importlib.import_module("writing_v1_handlers.finalize")

pytestmark = pytest.mark.offline

_REVIEW_JSON = {
    "verdict": "ship",
    "findings": [],
}


class _Out:
    def __init__(self, payload: dict) -> None:
        self.json = payload
        self.error = None
        self.model_id = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model_call_count = 0


class _Ctx:
    def __init__(self, outputs: dict, options: dict | None = None) -> None:
        self.outputs = outputs
        self.options = options or {"dispatch_thread_id": "15790"}
        self.execution_id = "run-openai-review"


class _Step:
    name = "review"


class _PostResp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _TransportLog:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        self.posts: list[dict[str, Any]] = []
        self.gets: list[str] = []

    async def post(self, path: str, json: dict[str, Any]) -> _PostResp:
        self.posts.append({"path": path, "json": json})
        model = str(json.get("model", ""))
        if model.startswith("anthropic/"):
            self.posts.append({"anthropic": True})
        if model.startswith("cdp/"):
            self.posts.append({"cdp": True})
        return _PostResp(self.status_code, dict(self.payload))

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.gets.append(path)
        return _GetResp(
            {"status": "completed", "result_text": json.dumps(_REVIEW_JSON)}
        )


class _GetResp:
    def __init__(self, data: dict[str, Any]) -> None:
        self.content = True
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _BusFake:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def post(self, path: str, json: dict[str, Any]) -> None:
        self.posts.append({"path": path, "json": json})


def _assemble_openai_writer() -> dict[str, Any]:
    return {
        "refused": None,
        "writer_seat": "cdp",
        "reviewer_seat": "openai",
        "writer_model": "anthropic/claude-sonnet-5-5",
        "reviewer_model": "openai/gpt-5.5",
        "documents_block": "",
        "brief_block": "",
        "pin_ledger": [],
        "dispatch_thread_id": "15790",
    }


def _draft() -> dict[str, Any]:
    return {
        "draft": "Draft body.",
        "claims": [],
        "need": [],
        "dispositions": [],
    }


@pytest.mark.asyncio
async def test_openai_reviewer_success_one_openai_call_recorded() -> None:
    transport = _TransportLog(
        200,
        {"execution_id": "exec-1", "result": _REVIEW_JSON},
    )
    handler = seat_dispatch.WritingReviewOpenaiHandler(client=transport)
    ctx = _Ctx(
        {
            "assemble": _Out(_assemble_openai_writer()),
            "draft": _Out(_draft()),
            "independence": _Out({"refused": None, "independence": "full"}),
        }
    )
    openai_out = await handler.execute(_Step(), ctx)
    assert openai_out.json["verdict"] == "ship"
    assert len([p for p in transport.posts if p.get("path")]) == 1
    assert not any(p.get("anthropic") for p in transport.posts)
    assert not any(p.get("cdp") for p in transport.posts)

    ctx.outputs["review_openai"] = openai_out
    ctx.outputs["provenance_check"] = _Out({"violations": [], "pass": True})
    selected = await seat_wait.WritingSeatSelectHandler().execute(_Step(), ctx)
    assert selected.json["verdict"] == "ship"
    ctx.outputs["review"] = selected
    fin = await finalize.WritingFinalizeHandler().execute(_Step(), ctx)
    assert fin.json["review"]["status"] == "ok"


@pytest.mark.asyncio
async def test_openai_reviewer_quota_held_bus_report_no_fallback() -> None:
    transport = _TransportLog(
        429,
        {"error_code": "insufficient_quota"},
    )
    bus = _BusFake()
    handler = seat_dispatch.WritingReviewOpenaiHandler(client=transport, bus_client=bus)
    ctx = _Ctx(
        {
            "assemble": _Out(_assemble_openai_writer()),
            "draft": _Out(_draft()),
        }
    )
    openai_out = await handler.execute(_Step(), ctx)
    assert openai_out.json["status"] == "held_reviewer_unfunded"
    assert len(transport.posts) == 1
    assert not any(p.get("anthropic") for p in transport.posts)
    assert not any(p.get("cdp") for p in transport.posts)
    assert bus.posts
    assert "unfunded" in bus.posts[0]["json"]["body"]

    ctx.outputs["review_openai"] = openai_out
    ctx.outputs["provenance_check"] = _Out({"violations": [], "pass": True})
    ctx.outputs["independence"] = _Out({"refused": None, "independence": "full"})
    selected = await seat_wait.WritingSeatSelectHandler().execute(_Step(), ctx)
    assert selected.json["status"] == "held_reviewer_unfunded"
    ctx.outputs["review"] = selected
    fin = await finalize.WritingFinalizeHandler().execute(_Step(), ctx)
    assert fin.json["review"]["status"] == "held_reviewer_unfunded"
    assert fin.json["ship_gate"]["pass"] is False
