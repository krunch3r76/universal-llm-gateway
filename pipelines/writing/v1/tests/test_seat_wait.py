"""Seat wait and select tests. The clock is injected. No live poll."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_PKG = "writing_v1_handlers"
if _PKG not in sys.modules:
    _HANDLERS = Path(__file__).resolve().parents[1] / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

seat_wait = importlib.import_module("writing_v1_handlers.seat_wait")
finalize = importlib.import_module("writing_v1_handlers.finalize")

pytestmark = pytest.mark.offline


class _Step:
    def __init__(self, name: str) -> None:
        self.name = name


class _Out:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        error: str | None = None,
        model_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model_call_count: int = 0,
    ) -> None:
        self.json = payload
        self.error = error
        self.model_id = model_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model_call_count = model_call_count
        self.raw = ""


class _Ctx:
    def __init__(self, outputs: dict[str, Any], **options: Any) -> None:
        self.outputs = outputs
        self.options = options


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status_code = 200
        self._body = body
        self.content = b"x"

    def json(self) -> dict[str, Any]:
        return self._body


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.gets: list[tuple[str, dict | None]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, _seconds: float) -> None:
        self.now += 1000

    async def get(self, path: str, params: dict | None = None) -> _Resp:
        self.gets.append((path, params))
        return _Resp({"status": "running"})


def _dispatch(**extra: Any) -> dict[str, Any]:
    payload = {
        "ok": True,
        "admit": "admitted",
        "seat": "cdp",
        "model": "cdp/opus-5.5",
        "execution_id": "e1",
        "idempotency_key": "writer-specialist-v1:run:writer",
    }
    payload.update(extra)
    return payload


def test_seat_wait_timeout_fallback() -> None:
    clock = _Clock()
    handler = seat_wait.WritingSeatWaitHandler(
        client=clock, now=clock, sleep=clock.sleep
    )
    waited = asyncio.run(
        handler.execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out(
                        {"sensitivity": "non_sensitive", "writer_seat": "cdp"}
                    ),
                    "draft_dispatch": _Out(_dispatch()),
                },
                seat_timeout_s=10,
            ),
        )
    ).json
    assert waited["seat_fallback"]["reason"] == "timeout"
    assert waited["fallback_to"] == "local"
    assert waited["seat_fallback"]["execution_id"] == "e1"

    local = _Out(
        {"draft": "local draft", "claims": [], "need": [], "dispositions": []},
        model_id="hermes",
        prompt_tokens=3,
        completion_tokens=4,
        model_call_count=1,
    )
    chosen = asyncio.run(
        seat_wait.WritingSeatSelectHandler().execute(
            _Step("draft"),
            _Ctx(
                {
                    "draft_wait": _Out(waited),
                    "draft_local": local,
                }
            ),
        )
    )
    assert chosen.json["draft"] == "local draft"
    assert chosen.json["seat_fallback"]["reason"] == "timeout"
    assert chosen.json["seat_record"]["seat"] == "local"
    assert chosen.model_id == "hermes"
    assert chosen.prompt_tokens == 3

    failed = asyncio.run(
        seat_wait.WritingSeatSelectHandler().execute(
            _Step("draft"),
            _Ctx(
                {
                    "draft_wait": _Out(waited),
                    "draft_local": _Out(None, error="boom"),
                }
            ),
        )
    ).json
    assert failed["refused"] == "writer_seat_failed"
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "ok": True,
                            "refused": None,
                            "output": "envelope",
                            "pin_ledger": [],
                            "writer_seat": "cdp",
                            "reviewer_seat": "local",
                        }
                    ),
                    "draft": _Out(failed),
                }
            ),
        )
    ).json
    assert envelope["refused"] == "writer_seat_failed"
    assert envelope["unsent"] is True
    assert envelope["seat_fallback"]["draft"]["reason"] == "timeout"


def test_seat_wait_success_parse() -> None:
    clock = _Clock()

    async def get(path: str, params: dict | None = None) -> _Resp:
        clock.gets.append((path, params))
        return _Resp(
            {
                "status": "completed",
                "result": {
                    "content": 'note {"draft": "D", "claims": [], "need": [], "dispositions": []}'
                },
            }
        )

    clock.get = get  # type: ignore[method-assign]
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "sensitive"}),
                    "draft_dispatch": _Out(_dispatch()),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["ok"] is True
    assert payload["result"]["draft"] == "D"


def test_seat_wait_unparseable() -> None:
    clock = _Clock()

    async def get(path: str, params: dict | None = None) -> _Resp:
        return _Resp({"status": "completed", "result": {"content": "not json"}})

    clock.get = get  # type: ignore[method-assign]
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "non_sensitive"}),
                    "draft_dispatch": _Out(_dispatch()),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["seat_fallback"]["reason"] == "unparseable"


def test_seat_wait_execution_failed() -> None:
    clock = _Clock()

    async def get(path: str, params: dict | None = None) -> _Resp:
        return _Resp({"status": "failed"})

    clock.get = get  # type: ignore[method-assign]
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "non_sensitive"}),
                    "draft_dispatch": _Out(_dispatch()),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["seat_fallback"]["reason"] == "execution_failed"


def test_seat_wait_admit_failed_no_get() -> None:
    clock = _Clock()
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "non_sensitive"}),
                    "draft_dispatch": _Out(_dispatch(admit="failed", ok=False)),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["seat_fallback"]["reason"] == "admit_failed"
    assert clock.gets == []


def test_seat_wait_reviewer_sensitive_no_local_fallback() -> None:
    clock = _Clock()
    waited = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("review_wait"),
            _Ctx(
                {
                    "assemble": _Out(
                        {"sensitivity": "sensitive", "reviewer_seat": "cdp"}
                    ),
                    "review_dispatch": _Out(
                        _dispatch(
                            seat="cdp",
                            idempotency_key="writer-specialist-v1:run:reviewer",
                        )
                    ),
                },
                seat_timeout_s=10,
            ),
        )
    ).json
    assert waited["fallback_to"] is None
    selected = asyncio.run(
        seat_wait.WritingSeatSelectHandler().execute(
            _Step("review"),
            _Ctx({"review_wait": _Out(waited)}),
        )
    ).json
    assert selected["refused"] == "reviewer_seat_failed"
    assert "verdict" not in selected
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "ok": True,
                            "refused": None,
                            "output": "envelope",
                            "pin_ledger": [],
                            "writer_seat": "local",
                            "reviewer_seat": "cdp",
                        }
                    ),
                    "draft": _Out(
                        {"draft": "D", "claims": [], "need": [], "dispositions": []},
                        model_id="hermes",
                    ),
                    "provenance_check": _Out({"violations": []}),
                    "independence": _Out({"refused": None, "independence": "full"}),
                    "review": _Out(selected),
                }
            ),
        )
    ).json
    assert envelope["review"]["status"] == "failed"
    assert envelope["ship_gate"]["pass"] is False


def test_seat_wait_reviewer_sensitive_violations_chain() -> None:
    clock = _Clock()
    waited = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("review_wait"),
            _Ctx(
                {
                    "assemble": _Out(
                        {"sensitivity": "sensitive", "reviewer_seat": "cdp"}
                    ),
                    "review_dispatch": _Out(
                        _dispatch(
                            seat="cdp",
                            idempotency_key="writer-specialist-v1:run:reviewer",
                        )
                    ),
                },
                seat_timeout_s=10,
            ),
        )
    ).json
    assert waited["fallback_to"] is None
    selected = asyncio.run(
        seat_wait.WritingSeatSelectHandler().execute(
            _Step("review"),
            _Ctx({"review_wait": _Out(waited)}),
        )
    ).json
    assert selected["refused"] == "reviewer_seat_failed"
    envelope = asyncio.run(
        finalize.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "ok": True,
                            "refused": None,
                            "output": "envelope",
                            "pin_ledger": [],
                            "writer_seat": "local",
                            "reviewer_seat": "cdp",
                        }
                    ),
                    "draft": _Out(
                        {"draft": "D", "claims": [], "need": [], "dispositions": []},
                        model_id="hermes",
                    ),
                    "provenance_check": _Out({"violations": [{"type": "omission"}]}),
                    "independence": _Out({"refused": None, "independence": "full"}),
                    "review": _Out(selected),
                }
            ),
        )
    )
    assert envelope.error is None
    assert envelope.json["draft_v1"]["draft"] == "D"
    assert envelope.json["review"]["status"] == "failed"
    assert envelope.json["ship_gate"]["pass"] is False


class _Bus:
    def __init__(self, plan: list[Any]) -> None:
        self.plan = list(plan)
        self.calls: list[str] = []

    async def get(self, path: str, params: dict | None = None) -> Any:
        del params
        self.calls.append(path)
        item = self.plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Tick:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _writer_body() -> dict[str, Any]:
    return {
        "body": json.dumps({"draft": "W", "claims": [], "need": [], "dispositions": []})
    }


def test_wait_bus_transient_by_number_error_then_success() -> None:
    import httpx

    clock = _Tick()
    bus = _Bus(
        [
            _Resp({"status": "complete", "qualifying_reply_turn": 3}),
            httpx.ReadTimeout("by-number"),
            _Resp({"status": "complete", "qualifying_reply_turn": 3}),
            _Resp(_writer_body()),
        ]
    )
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(bus=bus, now=clock, sleep=clock.sleep).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out(
                        {"sensitivity": "non_sensitive", "writer_seat": "cursor_pool1"}
                    ),
                    "draft_dispatch": _Out(
                        _dispatch(
                            seat="cursor_pool1",
                            poll_hint={
                                "tool": "agent_bus.wait",
                                "arguments": {"thread": "15790", "after_turn": 0},
                            },
                        )
                    ),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["ok"] is True
    assert payload["result"]["draft"] == "W"
    assert bus.calls.count("/turns/by-number") == 2


def test_wait_bus_persistent_by_number_error_times_out() -> None:
    import httpx

    clock = _Tick()
    bus = _Bus([httpx.ReadTimeout("by-number")] * 20)
    bus.plan.insert(0, _Resp({"status": "complete", "qualifying_reply_turn": 3}))

    async def sleep(_seconds: float) -> None:
        clock.now = 1000

    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(bus=bus, now=clock, sleep=sleep).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out(
                        {"sensitivity": "non_sensitive", "writer_seat": "cursor_pool1"}
                    ),
                    "draft_dispatch": _Out(
                        _dispatch(
                            seat="cursor_pool1",
                            poll_hint={
                                "arguments": {"thread": "15790", "after_turn": 0}
                            },
                        )
                    ),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["seat_fallback"]["reason"] == "timeout"
    assert payload["fallback_to"] == "local"
    assert clock.now >= 30


def test_wait_bus_wait_transport_error_retries() -> None:
    import httpx

    clock = _Tick()
    bus = _Bus(
        [
            httpx.ReadTimeout("wait"),
            _Resp({"status": "complete", "qualifying_reply_turn": 2}),
            _Resp(_writer_body()),
        ]
    )
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(bus=bus, now=clock, sleep=clock.sleep).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out(
                        {"sensitivity": "non_sensitive", "writer_seat": "cursor_pool1"}
                    ),
                    "draft_dispatch": _Out(
                        _dispatch(
                            seat="cursor_pool1",
                            poll_hint={"arguments": {"thread": "9", "after_turn": 0}},
                        )
                    ),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert payload["ok"] is True


def test_seat_wait_poll_404_is_terminal() -> None:
    clock = _Clock()

    async def get(path: str, params: dict | None = None) -> _Resp:
        clock.gets.append((path, params))
        resp = _Resp({"status": "running"})
        resp.status_code = 404
        return resp

    clock.get = get  # type: ignore[method-assign]
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "non_sensitive"}),
                    "draft_dispatch": _Out(_dispatch()),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert len(clock.gets) == 1
    assert payload["seat_fallback"]["reason"] == "poll_http_404"
    assert payload["fallback_to"] == "local"


def test_seat_wait_poll_503_retries_then_times_out() -> None:
    clock = _Tick()
    gets: list[str] = []

    class _Client:
        async def get(self, path: str, params: dict | None = None) -> _Resp:
            del params
            gets.append(path)
            resp = _Resp({"status": "running"})
            resp.status_code = 503
            return resp

    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=_Client(), now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "non_sensitive"}),
                    "draft_dispatch": _Out(_dispatch()),
                },
                seat_timeout_s=3,
            ),
        )
    ).json
    assert len(gets) > 1
    assert payload["seat_fallback"]["reason"] == "timeout"


def test_seat_wait_missing_execution_id_no_get() -> None:
    clock = _Clock()
    payload = asyncio.run(
        seat_wait.WritingSeatWaitHandler(
            client=clock, now=clock, sleep=clock.sleep
        ).execute(
            _Step("draft_wait"),
            _Ctx(
                {
                    "assemble": _Out({"sensitivity": "non_sensitive"}),
                    "draft_dispatch": _Out(_dispatch(execution_id="")),
                },
                seat_timeout_s=30,
            ),
        )
    ).json
    assert clock.gets == []
    assert payload["seat_fallback"]["reason"] == "admit_unknown"
