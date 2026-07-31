"""Opt-in integration tests — dispatch-surface-split Phase 5 gaps.

Unit coverage for the same contracts lives elsewhere:
- D3/D4: test_dispatch_surface.py (thread admission)
- D2/D6/E1 partial: test_async_tracker_delivery.py (on-behalf POST, no envelope)
- D6/D7: test_output_short_gating.py (output_short suppressed for thread contract)
- M1–M5: test_migration.py + test_frontier_registration.py

Run live slice:
  source ~/.gateway/secrets.env  # AGENT_BUS_TOKEN
  ULG_DISPATCH_INTEGRATION=1 pytest \\
    services/universal-stargate/systems/frontier_consult/test_dispatch_surface_integration.py -q

Requires: healthy Stargate, agent-bus, and a dispatchable model for the chosen role.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client, make_sync_client

_INTEGRATION = os.environ.get("ULG_DISPATCH_INTEGRATION") == "1"
_STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_PROBE_TIMEOUT = 2.0
_POLL_WAIT = 45.0
_MAX_POLLS = 40
_SECRETS_PATH = Path.home() / ".gateway" / "secrets.env"

pytestmark = pytest.mark.integration


def _skip_unless_integration() -> None:
    if not _INTEGRATION:
        pytest.skip(
            "Set ULG_DISPATCH_INTEGRATION=1 to run live dispatch-surface integration"
        )


def _load_bus_token() -> str:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if token:
        return token
    if _SECRETS_PATH.is_file():
        for line in _SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("AGENT_BUS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _dispatch_thread_id(suffix: str) -> str:
    return f"{suffix}-{uuid.uuid4().hex[:8]}"


def _bus_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _looks_like_metadata_envelope(body: str) -> bool:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    blob = json.dumps(parsed).lower()
    return "execution_id" in parsed and ("poll" in blob or "pipeline" in blob)


@pytest.fixture(scope="module", autouse=True)
def _require_live_services() -> None:
    _skip_unless_integration()
    try:
        with httpx.Client(base_url=_STARGATE_URL, timeout=_PROBE_TIMEOUT) as client:
            response = client.get("/v1/models")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        pytest.skip(f"Stargate at {_STARGATE_URL} unreachable: {exc}")
    if response.status_code >= 500:
        pytest.skip(
            f"Stargate at {_STARGATE_URL} unhealthy: HTTP {response.status_code}"
        )
    token = _load_bus_token()
    if not token:
        pytest.skip("AGENT_BUS_TOKEN not set (env or ~/.gateway/secrets.env)")
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_PROBE_TIMEOUT) as bus:
            probe = bus.get(
                "/threads", headers=_bus_headers(token), params={"limit": 1}
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        pytest.skip(f"Agent-bus unreachable: {exc}")
    if probe.status_code == 401:
        pytest.skip("AGENT_BUS_TOKEN rejected by agent-bus")


async def _create_open_thread(
    bus: httpx.AsyncClient,
    *,
    token: str,
    slug: str,
) -> str:
    payload = {
        "slug": slug,
        "from": "cursor",
        "to": "cursor",
        "subject": f"dispatch-surface integration {slug}",
        "body": "Seed turn for dispatch-surface-split live tests.",
        "status": "open",
        "after_turn": 0,
        "tags": ["type:test", "dispatch-surface-split"],
    }
    resp = await bus.post(
        "/threads/with-turn",
        headers=_bus_headers(token),
        json=payload,
    )
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    thread = data.get("thread") or {}
    thread_id = str(thread.get("id") or data.get("thread_id") or data.get("id"))
    assert thread_id.isdigit(), data
    return thread_id


async def _admit_to_thread(
    stargate: httpx.AsyncClient,
    *,
    thread_id: str,
    dispatch_thread_id: str,
    content: str,
    model: str = "openai/gpt-5.4-mini",
    max_tool_turns: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "op": "to_thread",
        "role": "gatherer",
        "dispatch_thread_id": dispatch_thread_id,
        "thread": thread_id,
        "caller_agent": "cursor",
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }
    if max_tool_turns is not None:
        body["max_tool_turns"] = max_tool_turns
    resp = await stargate.post("/api/v1/team/dispatch", json=body)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data.get("execution_id")
    assert data.get("status") == "running"
    return data


async def _poll_execution(
    stargate: httpx.AsyncClient,
    execution_id: str,
    *,
    wait: float = _POLL_WAIT,
    terminal: frozenset[str] = frozenset({"completed", "failed"}),
) -> dict[str, Any]:
    for _ in range(_MAX_POLLS):
        resp = await stargate.get(
            f"/api/v1/pipelines/executions/{execution_id}",
            params={"wait": wait},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data.get("status") in terminal:
            return data
    pytest.fail(f"execution {execution_id} did not reach terminal state")


async def _wait_for_thread_delivery(
    stargate: httpx.AsyncClient,
    bus: httpx.AsyncClient,
    *,
    token: str,
    execution_id: str,
    thread_id: str,
    from_agent: str,
    baseline_role_turns: int,
) -> dict[str, Any]:
    """Bus delivery runs after tracker terminal; poll until turn lands or timeout."""
    terminal = await _poll_execution(stargate, execution_id)
    for _ in range(20):
        if terminal.get("thread_reply_observed_at"):
            break
        role_turns = await _list_role_turns(
            bus, token=token, thread_id=thread_id, from_agent=from_agent
        )
        if len(role_turns) > baseline_role_turns:
            break
        await asyncio.sleep(0.25)
        peek = await stargate.get(
            f"/api/v1/pipelines/executions/{execution_id}",
            params={"wait": 0},
        )
        if peek.status_code == 200:
            terminal = peek.json()
    return terminal


async def _list_role_turns(
    bus: httpx.AsyncClient,
    *,
    token: str,
    thread_id: str,
    from_agent: str,
) -> list[dict[str, Any]]:
    resp = await bus.get(
        "/turns",
        headers=_bus_headers(token),
        params={"thread": thread_id, "last": 20, "compact": False},
    )
    assert resp.status_code == 200, resp.text
    turns = resp.json().get("turns") or []
    return [
        t
        for t in turns
        if t.get("from") == from_agent or t.get("from_agent") == from_agent
    ]


@pytest.mark.asyncio
async def test_s4_to_thread_happy_path_live() -> None:
    """S4 — op=to_thread end-to-end: dispatch completes and bus turn is posted."""
    token = _load_bus_token()
    slug = _dispatch_thread_id("s4")
    dispatch_thread_id = _dispatch_thread_id("s4-dispatch")

    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as stargate:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as bus:
            thread_id = await _create_open_thread(bus, token=token, slug=slug)
            before_turns = await _list_role_turns(
                bus, token=token, thread_id=thread_id, from_agent="gatherer"
            )

            admit = await _admit_to_thread(
                stargate,
                thread_id=thread_id,
                dispatch_thread_id=dispatch_thread_id,
                content="Reply with exactly the token DISPATCH_OK and nothing else.",
            )
            terminal = await _wait_for_thread_delivery(
                stargate,
                bus,
                token=token,
                execution_id=admit["execution_id"],
                thread_id=thread_id,
                from_agent="gatherer",
                baseline_role_turns=len(before_turns),
            )
            assert terminal["status"] == "completed", terminal
            assert terminal.get("op") == "to_thread"
            assert terminal.get("target_thread") == thread_id

            after_turns = await _list_role_turns(
                bus, token=token, thread_id=thread_id, from_agent="gatherer"
            )
            assert len(after_turns) == len(before_turns) + 1
            latest = after_turns[-1]
            body = latest.get("body") or ""
            assert body.strip()
            assert not _looks_like_metadata_envelope(body)


@pytest.mark.asyncio
async def test_d1_tracker_running_before_completion_live() -> None:
    """D1 — immediate poll shows running before terminal completed."""
    token = _load_bus_token()
    slug = _dispatch_thread_id("d1")
    dispatch_thread_id = _dispatch_thread_id("d1-dispatch")

    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as stargate:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as bus:
            thread_id = await _create_open_thread(bus, token=token, slug=slug)
            admit = await _admit_to_thread(
                stargate,
                thread_id=thread_id,
                dispatch_thread_id=dispatch_thread_id,
                content=(
                    "Write a thorough three-paragraph summary of dispatch "
                    "surface split testing goals."
                ),
                model="openai/gpt-5.4",
            )
            running = await stargate.get(
                f"/api/v1/pipelines/executions/{admit['execution_id']}",
                params={"wait": 0},
            )
            assert running.status_code == 200, running.text
            running_data = running.json()
            assert running_data["status"] == "running", running_data

            terminal = await _poll_execution(stargate, admit["execution_id"])
            assert terminal["status"] == "completed", terminal


@pytest.mark.asyncio
async def test_d5_cancel_mid_flight_live() -> None:
    """D5 — cancel is non-transactional; terminal status reflects cancellation."""
    token = _load_bus_token()
    slug = _dispatch_thread_id("d5")
    dispatch_thread_id = _dispatch_thread_id("d5-dispatch")

    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as stargate:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as bus:
            thread_id = await _create_open_thread(bus, token=token, slug=slug)
            admit = await _admit_to_thread(
                stargate,
                thread_id=thread_id,
                dispatch_thread_id=dispatch_thread_id,
                content=(
                    "Perform a multi-step research pass: query cortex for open "
                    "todos, then rag for dispatch-surface-split, then synthesize "
                    "a long structured report with sections and citations."
                ),
                model="openai/gpt-5.4",
                max_tool_turns=8,
            )
            execution_id = admit["execution_id"]

            await asyncio.sleep(0.05)
            cancel_resp = await stargate.delete(
                f"/api/v1/pipelines/executions/{execution_id}"
            )
            assert cancel_resp.status_code == 200, cancel_resp.text
            payload = cancel_resp.json()

            if payload.get("status") == "completed":
                pytest.skip(
                    "Dispatch completed before cancel landed — rerun under load "
                    "or with a slower model"
                )

            assert payload["status"] == "failed", payload
            error = payload.get("error") or {}
            assert error.get("code") == "pipeline_execution_cancelled", payload


@pytest.mark.asyncio
async def test_e1_no_metadata_envelope_live() -> None:
    """E1 — to_thread yields one stargate-posted reply, no output_short hint."""
    token = _load_bus_token()
    slug = _dispatch_thread_id("e1")
    dispatch_thread_id = _dispatch_thread_id("e1-dispatch")

    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as stargate:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as bus:
            thread_id = await _create_open_thread(bus, token=token, slug=slug)
            admit = await _admit_to_thread(
                stargate,
                thread_id=thread_id,
                dispatch_thread_id=dispatch_thread_id,
                content="Reply briefly: integration test E1 ack.",
            )
            terminal = await _wait_for_thread_delivery(
                stargate,
                bus,
                token=token,
                execution_id=admit["execution_id"],
                thread_id=thread_id,
                from_agent="gatherer",
                baseline_role_turns=0,
            )
            assert terminal["status"] == "completed", terminal

            hints = (terminal.get("result") or {}).get("hints") or []
            output_short = [h for h in hints if h.get("type") == "output_short"]
            assert not output_short, hints

            role_turns = await _list_role_turns(
                bus, token=token, thread_id=thread_id, from_agent="gatherer"
            )
            assert len(role_turns) == 1
            body = role_turns[0].get("body") or ""
            assert body.strip()
            assert not _looks_like_metadata_envelope(body)
            assert "output_short" not in body.lower()
