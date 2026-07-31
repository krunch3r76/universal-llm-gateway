"""End-to-end persistence tests for team-dispatch (Phase D).

Live-service integration: ``POST /api/v1/team/dispatch`` with
``dispatch_thread_id`` and poll ``GET /api/v1/pipelines/executions/{id}``.

Mirrors ``tests/test_cortex_chat_openai_persistence.py`` (11-turn archive
parity, recall, thread isolation).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

import httpx
import pytest

_STARGATE_PATH = (
    __import__("pathlib").Path(__file__).resolve().parents[1]
    / "services"
    / "universal-stargate"
)
if str(_STARGATE_PATH) not in sys.path:
    sys.path.insert(0, str(_STARGATE_PATH))

import importlib.util

_events_path = (
    _STARGATE_PATH / "systems/pipeline/core/handlers/thread_persistence/events.py"
)
_spec = importlib.util.spec_from_file_location("_tp_events", _events_path)
_events_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_events_mod)
cx_async = _events_mod.cx_async

_STARGATE_URL = "http://localhost:9999"
_PROBE_TIMEOUT = 2.0
_POLL_WAIT = 45.0
_MAX_POLLS = 40


def _dispatch_thread_id(suffix: str) -> str:
    return f"{suffix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module", autouse=True)
def _require_live_stargate() -> None:
    try:
        with httpx.Client(base_url=_STARGATE_URL, timeout=_PROBE_TIMEOUT) as c:
            response = c.get("/v1/models")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        pytest.skip(f"Stargate at {_STARGATE_URL} unreachable: {exc}")
    if response.status_code >= 500:
        pytest.skip(
            f"Stargate at {_STARGATE_URL} unhealthy: HTTP {response.status_code}"
        )


async def _count_turn_assertions(anchor_id: str, predicate_prefix: str) -> list[int]:
    res = await cx_async(
        "assertions",
        {"entity_id": anchor_id, "superseded": False},
    )
    assertions = res.get("items") or res.get("assertions") or []
    indices: list[int] = []
    for a in assertions:
        pf = a.get("predicate_form") or ""
        if not pf.startswith(predicate_prefix):
            continue
        inner = pf[len(predicate_prefix) : -1]
        try:
            indices.append(int(inner))
        except ValueError:
            continue
    return sorted(indices)


async def _poll_terminal(
    client: httpx.AsyncClient, execution_id: str
) -> dict[str, Any]:
    for _ in range(_MAX_POLLS):
        resp = await client.get(
            f"/api/v1/pipelines/executions/{execution_id}",
            params={"wait": _POLL_WAIT},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data.get("status") in ("completed", "failed"):
            return data
    pytest.fail(f"execution {execution_id} did not reach terminal state")


async def _team_generate(
    client: httpx.AsyncClient,
    *,
    dispatch_thread_id: str,
    content: str,
    model: str = "openai/gpt-5.5",
) -> dict[str, Any]:
    admit = await client.post(
        "/api/v1/team/dispatch",
        json={
            "op": "generate",
            "role": "reviewer",
            "dispatch_thread_id": dispatch_thread_id,
            "model": model,
            "messages": [{"role": "user", "content": content}],
        },
    )
    assert admit.status_code == 202, admit.text
    execution_id = admit.json()["execution_id"]
    terminal = await _poll_terminal(client, execution_id)
    assert terminal["status"] == "completed", terminal
    return terminal


@pytest.mark.asyncio
async def test_e2e_eleven_turn_team_dispatch_archive_parity() -> None:
    """11 team-dispatch turns archive user+assistant on dispatch anchor."""
    dispatch_thread_id = _dispatch_thread_id("e2e-team-11")
    n_turns = 11
    seed_token = "PHASED11"
    anchor_id = f"thread:dispatch:{dispatch_thread_id}"

    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as client:
        for i in range(n_turns):
            content = (
                f"Turn {i}: remember token {seed_token}{i:02d}."
                if i == 0
                else f"Turn {i}: ack prior tokens; token {seed_token}{i:02d}."
            )
            await _team_generate(
                client,
                dispatch_thread_id=dispatch_thread_id,
                content=content,
            )

        await _team_generate(
            client,
            dispatch_thread_id=dispatch_thread_id,
            content=f"What was the token from turn 0 ({seed_token}00)?",
        )

        user_turns = await _count_turn_assertions(anchor_id, "user_turn(")
        assistant_turns = await _count_turn_assertions(anchor_id, "assistant_turn(")
        assert len(user_turns) >= n_turns, (
            f"expected >={n_turns} user_turn assertions, got {user_turns}"
        )
        assert len(assistant_turns) >= n_turns, (
            f"expected >={n_turns} assistant_turn assertions, got {assistant_turns}"
        )


@pytest.mark.asyncio
async def test_dispatch_thread_id_binding_isolation() -> None:
    """Distinct dispatch_thread_ids use disjoint cortex anchors."""
    thread_a = _dispatch_thread_id("team-session-A")
    thread_b = _dispatch_thread_id("team-session-B")
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as client:
        await _team_generate(
            client,
            dispatch_thread_id=thread_a,
            content="Identify as Dispatch Alpha.",
        )
        await _team_generate(
            client,
            dispatch_thread_id=thread_b,
            content="Identify as Dispatch Beta.",
        )
        a_turns = await _count_turn_assertions(
            f"thread:dispatch:{thread_a}", "user_turn("
        )
        b_turns = await _count_turn_assertions(
            f"thread:dispatch:{thread_b}", "user_turn("
        )
        assert len(a_turns) >= 1
        assert len(b_turns) >= 1


@pytest.mark.asyncio
async def test_concurrent_dispatch_thread_serialization() -> None:
    """Concurrent dispatches on one dispatch_thread_id serialize cleanly."""
    dispatch_thread_id = _dispatch_thread_id("concurrent-dispatch-lock")
    n = 3
    anchor_id = f"thread:dispatch:{dispatch_thread_id}"

    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as client:
        coros = [
            _team_generate(
                client,
                dispatch_thread_id=dispatch_thread_id,
                content=f"Parallel team turn {i}",
            )
            for i in range(n)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors

        user_turns = await _count_turn_assertions(anchor_id, "user_turn(")
        assert len(user_turns) >= n
        assert len(set(user_turns)) == len(user_turns)
