"""Engine-driven smoke fixtures for maestro-induct."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from systems.pipeline.core.executor.pipeline_executor import PipelineExecutor
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.user_handlers import load_user_handlers

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "libs" / "maestro_induct" / "fixtures"


def thread_ladder(
    *,
    root: str,
    top: int,
    cps: dict[int, str],
    superseded: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n in range(1, top + 1):
        created = f"2026-09-22T00:{n % 60:02d}:00Z"
        rows.append(
            {
                "id": 1_000_000 + n,
                "thread": root,
                "turn_number": n,
                "from": "cursor",
                "to": "web-anthropic",
                "subject": cps.get(n, f"NOTE ladder filler {n}"),
                "status": "superseded" if n in superseded else "open",
                "supersedes_turn": None,
                "created_at": created,
                "read_at": created,
                "body": None,
                "attachments": None,
            }
        )
    return rows


class SmokeRouter:
    def __init__(self, *, ladder: list[dict[str, Any]], overrides: dict[Any, Any] | None = None):
        self.ladder = ladder
        self.overrides = overrides or {}
        self.requests: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []
        self.unrouted: list[tuple[str, str]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path
        params = dict(request.url.params)
        body = None
        if request.content:
            try:
                body = json.loads(request.content.decode())
            except json.JSONDecodeError:
                body = None
        self.requests.append((method, path, params, body))
        key = (method, path, tuple(sorted((params or {}).items())))
        if key in self.overrides:
            payload = self.overrides[key]
            if callable(payload):
                payload = payload()
            return httpx.Response(200, json=payload)

        if method == "GET" and path.rstrip("/").endswith("/threads/12286/lineage"):
            return httpx.Response(200, json=json.loads((FIXTURES / "lineage-12286.json").read_text()))
        if method == "GET" and path == "/turns/by-number":
            tn = int(params.get("turn_number", "0"))
            fp = FIXTURES / f"12286-cp-turn-{tn}.json"
            if fp.is_file():
                return httpx.Response(200, json=json.loads(fp.read_text()))
            return httpx.Response(404, json={"detail": "missing"})
        if method == "GET" and path == "/turns/unread-toc":
            return httpx.Response(
                200, json=json.loads((FIXTURES / "unread-toc-web-anthropic.json").read_text())
            )
        if method == "POST" and path == "/dispatch":
            tool = (body or {}).get("tool")
            if tool == "relationships":
                return httpx.Response(
                    200,
                    json=json.loads(
                        (FIXTURES / "relationships-document-12286-continuity.json").read_text()
                    ),
                )
        if method == "GET" and path.rstrip("/") == "/turns":
            thread = params.get("thread")
            if params.get("to") == "web-anthropic" and params.get("unread") == "true":
                mapping = {
                    "12286": "turns-12286-unread-web-anthropic.json",
                    "12359": "turns-12359-unread-web-anthropic.json",
                    "12291": "turns-12291-last6.json",
                }
                fn = mapping.get(str(thread))
                if fn:
                    return httpx.Response(200, json=json.loads((FIXTURES / fn).read_text()))
                return httpx.Response(200, json={"turns": []})
            if params.get("last") == "4" and thread:
                if str(thread) == "12291":
                    return httpx.Response(
                        200, json=json.loads((FIXTURES / "turns-12291-last6.json").read_text())
                    )
                return httpx.Response(200, json={"turns": []})
            # checkpoint reads
            include_sup = params.get("include_superseded") == "true"
            rows = [t for t in self.ladder if str(t.get("thread")) == str(thread)]
            if params.get("after_turn") is not None:
                after = int(params["after_turn"])
                last = int(params.get("last") or 1000)
                rows = [t for t in rows if int(t["turn_number"]) > after]
                rows.sort(key=lambda t: int(t["turn_number"]))
                rows = rows[:last]
            else:
                last = int(params.get("last") or 50)
                rows.sort(key=lambda t: int(t["turn_number"]), reverse=True)
                rows = rows[:last]
            if not include_sup:
                rows = [t for t in rows if t.get("status") != "superseded"]
            return httpx.Response(200, json={"turns": rows})

        self.unrouted.append((method, path))
        return httpx.Response(404, json={"detail": "unrouted"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.fixture(scope="session")
def engine_registry(tmp_path_factory: pytest.TempPathFactory) -> PipelineRegistry:
    root = tmp_path_factory.mktemp("engine") / "pipelines"
    shutil.copytree(
        REPO / "pipelines" / "maestro_induct",
        root / "maestro_induct",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    snap = root.parent / "snap"
    snap.mkdir(exist_ok=True)
    prev = None
    import os

    if "STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR" in os.environ:
        prev = os.environ.pop("STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR")
    load_user_handlers(config_base_dir=root)
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
        snapshot_dir=snap,
    )
    registry.load()
    assert registry.is_pipeline("maestro-induct"), registry._validation_errors
    if prev is not None:
        os.environ["STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR"] = prev
    return registry


@pytest.fixture
def cortex_files_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def staged_cortex(cortex_files_root: Path):
    staging = [
        ("12286-card.md", "notes/system/threads/12286-card.md"),
        ("12286-continuity.md", "notes/system/threads/12286-continuity.md"),
        ("12286-journal.md", "notes/system/threads/12286-journal.md"),
        ("12286-house-runbook.md", "notes/runbooks/12286-house.md"),
        ("maestro-induct-pipeline-scoreboard.md", "notes/system/scoreboards/maestro-induct-pipeline-scoreboard.md"),
    ]
    for src, dest in staging:
        target = cortex_files_root / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / src, target)


@pytest.fixture
def smoke_router(monkeypatch, request):
    cp17 = json.loads((FIXTURES / "12286-cp-turn-17.json").read_text())
    cp35 = json.loads((FIXTURES / "12286-cp-turn-35.json").read_text())
    cp138 = json.loads((FIXTURES / "12286-cp-turn-138.json").read_text())
    cps = {17: cp17["subject"], 35: cp35["subject"], 138: cp138["subject"]}
    param = getattr(request, "param", None) or {}
    top = param.get("top", 2571)
    superseded = param.get("superseded", frozenset({17, 35}))
    ladder = thread_ladder(root="12286", top=top, cps=cps, superseded=superseded)
    router = SmokeRouter(ladder=ladder)
    mod = sys.modules.get("_pipeline_handlers_maestro_induct_v1")
    assert mod is not None, "handlers not loaded"
    clients = mod._clients
    factories: list[Any] = []

    def factory(base, *args, timeout=None, **kwargs):
        factories.append(timeout)

        class _Holder:
            async def __aenter__(self):
                self._client = httpx.AsyncClient(
                    base_url=base, transport=router.transport, timeout=timeout
                )
                return self._client

            async def __aexit__(self, *exc):
                await self._client.aclose()

        return _Holder()

    async def _bus_get(path, *, params=None, deadline_epoch=None):
        req = httpx.Request("GET", f"http://agent-bus{path}", params=params or {})
        resp = await router.handle(req)
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"error": {"code": f"http_{resp.status_code}"}}
        return payload, resp.status_code

    async def _cortex_dispatch(tool, arguments, *, deadline_epoch=None):
        req = httpx.Request(
            "POST",
            "http://cortex/dispatch",
            json={"tool": tool, "arguments": json.dumps(arguments)},
        )
        resp = await router.handle(req)
        return resp.json()

    monkeypatch.setattr(clients, "bus_get", _bus_get)
    monkeypatch.setattr(clients, "cortex_dispatch", _cortex_dispatch)
    router.factories = factories
    return router


@pytest.fixture
def pipeline_executor(engine_registry: PipelineRegistry):
    request_executor = types.SimpleNamespace()

    async def execute_request(self, context):  # noqa: ANN001
        raise AssertionError("maestro-induct has no model step")

    request_executor.execute_request = execute_request
    proxy = types.SimpleNamespace(event_bus=None)
    return PipelineExecutor(engine_registry, request_executor, proxy)


async def run_smoke(executor: PipelineExecutor, *, options: dict[str, Any] | None = None):
    from maestro_induct.assemble import dumps_packet

    opts = options or {
        "root": "12286",
        "journal_entries": 2,
        "include_runbook": True,
        "include_lanes": True,
    }

    async def _never_disconnect():
        await asyncio.Event().wait()

    ctx = types.SimpleNamespace(
        selected_model="maestro-induct",
        original_request={
            "model": "maestro-induct",
            "messages": [{"role": "user", "content": "MAESTRO 12286"}],
            "pipeline_options": opts,
        },
        chat_request=None,
        http_request=types.SimpleNamespace(receive=_never_disconnect),
        outputs={},
        options=opts,
        execution_id="test",
    )
    resp = await executor.execute(ctx)
    assert resp.status_code == 200
    outer = json.loads(resp.body)
    content = outer["choices"][0]["message"]["content"]
    packet = json.loads(content)
    assert content == dumps_packet(packet)
    return packet, content
