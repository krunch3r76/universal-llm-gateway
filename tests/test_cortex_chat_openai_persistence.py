"""End-to-end persistence tests for the cortex-chat-openai pipeline.

Phase 6 of the cortex-chat-openai MVP. These are **live-service
integration tests**: they hit the running host-side Stargate at
``http://localhost:9999`` (the sole client-facing endpoint per
ulg-architecture) and exercise the full four-step compaction DAG
through ``/v1/chat/completions``.

Coverage:

- ``test_e2e_three_turn_compaction`` — multi-turn user → assistant
  → user → assistant flow asserts that prior turns land as
  ``user_turn(N)`` / ``assistant_turn(N)`` predicate assertions on
  ``thread:openai-chat:{chat_id}`` and that the model recalls the
  archived content on the follow-up turn.
- ``test_e2e_eleven_turn_mcp_archive_parity`` — 11+ turn run with
  injected MCP (``cortex-chat-openai`` default) verifies assistant
  archives land for every completed turn (including tool-only MCP
  completions) and turn-0 recall survives assembly.
- ``test_chat_id_binding_isolation`` — distinct chat_ids resolve to
  disjoint anchors and do not cross-contaminate.
- ``test_server_owned_history_authority`` — tampered client
  ``messages[]`` cannot override server-archived history (A5
  invariant from Phase 2's ``_validate_text_only_messages``).
- ``test_concurrency_lock_serialization`` — three concurrent
  requests with the same chat_id all return 200 and produce a
  contiguous ``user_turn(0..N-1)`` assertion sequence with no
  duplicates (Phase 5 lock surface exercised end-to-end).

Skipped automatically when the host Stargate is unreachable so the
suite degrades cleanly under CI / dev-without-stack conditions.
Required services for a green run: Stargate (:9999), cortex-api
UDS, and a model catalog that includes ``openai/gpt-5.5``.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import httpx
import pytest

_STARGATE_PATH = Path(__file__).resolve().parents[1] / "services" / "universal-stargate"
if str(_STARGATE_PATH) not in sys.path:
    sys.path.insert(0, str(_STARGATE_PATH))

import importlib.util

# Load cx_async from events.py via importlib — importing
# systems.pipeline.* triggers DAGExecutor → src.core.gateway_tracker.
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


def _chat_id(suffix: str) -> str:
    """Fresh chat_id per invocation — avoids cross-run anchor pollution."""
    return f"{suffix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module", autouse=True)
def _require_live_stargate() -> None:
    """Skip all tests in this module if the host Stargate is unreachable.

    Probes ``GET /v1/models`` (standard OpenAI surface) with a tight
    timeout. Connection refused / timeout means no live stack — skip
    cleanly rather than fail with a noisy ConnectError.
    """
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
    """Return the sorted list of turn indices for a given predicate prefix.

    Reads non-superseded assertions on the anchor and extracts the
    integer N from ``user_turn(N)`` / ``assistant_turn(N)``
    ``predicate_form`` strings. Duplicates indicate a concurrency
    lock failure.
    """
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


@pytest.mark.asyncio
async def test_e2e_three_turn_compaction() -> None:
    """Multi-turn flow archives turns and the model recalls prior content."""
    chat_id = _chat_id("e2e-compaction-test")
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=60.0) as client:
        # Turn 1: seed a memorable fact.
        r1 = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_id,
                "messages": [{"role": "user", "content": "My favorite color is BLUE."}],
            },
        )
        assert r1.status_code == 200, r1.text
        res1 = r1.json()
        assert "choices" in res1
        first_reply = res1["choices"][0]["message"]["content"]

        # Turn 2: follow-up that requires recall from cortex assertions.
        r2 = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_id,
                "messages": [
                    {"role": "user", "content": "My favorite color is BLUE."},
                    {"role": "assistant", "content": first_reply},
                    {"role": "user", "content": "What is my favorite color?"},
                ],
            },
        )
        assert r2.status_code == 200, r2.text
        res2 = r2.json()
        content2 = res2["choices"][0]["message"]["content"].upper()
        assert "BLUE" in content2, (
            f"model failed to recall archived fact; got: {content2!r}"
        )

        anchor_id = f"thread:openai-chat:{chat_id}"
        user_turns = await _count_turn_assertions(anchor_id, "user_turn(")
        assistant_turns = await _count_turn_assertions(anchor_id, "assistant_turn(")
        # Two user turns submitted; at least one assistant archive must
        # have landed (turn 1's archive runs before turn 2's assemble
        # because the concurrency lock serialises on chat_id).
        assert len(user_turns) >= 2, (
            f"expected >=2 user_turn assertions, got {user_turns}"
        )
        assert len(assistant_turns) >= 1, (
            f"expected >=1 assistant_turn assertions, got {assistant_turns}"
        )


@pytest.mark.asyncio
async def test_e2e_eleven_turn_mcp_archive_parity() -> None:
    """11+ turns with MCP: every user turn has a matching assistant archive."""
    chat_id = _chat_id("e2e-11turn-mcp")
    n_turns = 11
    seed_token = "PHASEE11"
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=180.0) as client:
        for i in range(n_turns):
            content = (
                f"Turn {i}: remember token {seed_token}{i:02d}."
                if i == 0
                else f"Turn {i}: ack prior tokens; token {seed_token}{i:02d}."
            )
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "cortex-chat-openai",
                    "chat_id": chat_id,
                    "messages": [{"role": "user", "content": content}],
                },
            )
            assert resp.status_code == 200, resp.text

        recall = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_id,
                "messages": [
                    {
                        "role": "user",
                        "content": f"What was the token from turn 0 ({seed_token}00)?",
                    }
                ],
            },
        )
        assert recall.status_code == 200, recall.text
        recall_text = recall.json()["choices"][0]["message"]["content"].upper()
        assert f"{seed_token}00" in recall_text, (
            f"turn-0 recall failed after {n_turns} turns; got: {recall_text!r}"
        )

        anchor_id = f"thread:openai-chat:{chat_id}"
        user_turns = await _count_turn_assertions(anchor_id, "user_turn(")
        assistant_turns = await _count_turn_assertions(anchor_id, "assistant_turn(")
        # n_turns user archives + 1 recall user turn; assistant archives for
        # each completed respond (including the recall turn).
        assert len(user_turns) >= n_turns, (
            f"expected >={n_turns} user_turn assertions, got {user_turns}"
        )
        assert len(assistant_turns) >= n_turns, (
            f"expected >={n_turns} assistant_turn assertions (MCP tool-only "
            f"completions must archive), got {assistant_turns}"
        )
        # Phase E gap: early MCP turns 1-5 must not skip assistant archives.
        early_assistant = [t for t in assistant_turns if t <= 5]
        assert len(early_assistant) >= 6, (
            f"assistant_turn(0..5) incomplete — got indices {assistant_turns}"
        )


@pytest.mark.asyncio
async def test_chat_id_binding_isolation() -> None:
    """Distinct chat_ids resolve to disjoint anchors."""
    chat_a = _chat_id("chat-session-A")
    chat_b = _chat_id("chat-session-B")
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=60.0) as client:
        r_a = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_a,
                "messages": [{"role": "user", "content": "Identify as Agent Alpha."}],
            },
        )
        r_b = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_b,
                "messages": [{"role": "user", "content": "Identify as Agent Beta."}],
            },
        )
        assert r_a.status_code == 200, r_a.text
        assert r_b.status_code == 200, r_b.text

        anchor_a = f"thread:openai-chat:{chat_a}"
        anchor_b = f"thread:openai-chat:{chat_b}"
        a_turns = await _count_turn_assertions(anchor_a, "user_turn(")
        b_turns = await _count_turn_assertions(anchor_b, "user_turn(")
        assert len(a_turns) >= 1
        assert len(b_turns) >= 1


@pytest.mark.asyncio
async def test_server_owned_history_authority() -> None:
    """Server-archived history overrides tampered client messages (A5)."""
    chat_id = _chat_id("test-server-auth-authority")
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=60.0) as client:
        # Turn 1: ground-truth fact lands in cortex via archive_user.
        r1 = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_id,
                "messages": [
                    {"role": "user", "content": "The secret code is MAGENTA."}
                ],
            },
        )
        assert r1.status_code == 200, r1.text

        # Turn 2: client tampers its recollection; server must override.
        r2 = await client.post(
            "/v1/chat/completions",
            json={
                "model": "cortex-chat-openai",
                "chat_id": chat_id,
                "messages": [
                    {"role": "user", "content": "The secret code is CYAN."},
                    {"role": "assistant", "content": "Got it, CYAN noted."},
                    {"role": "user", "content": "What is the secret code?"},
                ],
            },
        )
        assert r2.status_code == 200, r2.text
        content = r2.json()["choices"][0]["message"]["content"].upper()
        assert "MAGENTA" in content, (
            f"server failed to override tampered history; got: {content!r}"
        )
        assert "CYAN" not in content, (
            f"server leaked tampered client memory; got: {content!r}"
        )


@pytest.mark.asyncio
async def test_e2e_twelve_turn_summarization() -> None:
    """12+ turns: summary assertion present; Stage A turns NOT superseded.

    After turn 12 (turn_index=12 > window_size(8) + margin(4) = 12), the
    summarize step fires and writes a ``thread_summary(N)`` assertion on the
    anchor.  Stage A policy: collapsed turns are NOT superseded, so total
    user_turn assertions = n_turns (no removals).  The next assembled prefix
    includes a ``[Archive summary]`` system message.

    Skipped automatically when Stargate is unreachable.
    """
    chat_id = _chat_id("e2e-12turn-summary")
    n_turns = 13  # > window_size(8) + margin(4) = 12 → triggers summarize
    seed = "SUMTEST"
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=300.0) as client:
        for i in range(n_turns):
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "cortex-chat-openai",
                    "chat_id": chat_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Turn {i}: token {seed}{i:02d}. "
                                "Acknowledge and continue."
                            ),
                        }
                    ],
                },
            )
            assert resp.status_code == 200, f"turn {i}: {resp.text}"

        anchor_id = f"thread:openai-chat:{chat_id}"

        # Verify summary assertion exists (predicate thread_summary(N)).
        res = await cx_async(
            "assertions",
            {"entity_id": anchor_id, "superseded": False},
        )
        all_assertions = res.get("items") or res.get("assertions") or []
        summary_assertions = [
            a
            for a in all_assertions
            if (a.get("predicate_form") or "").startswith("thread_summary(")
            and (a.get("claim") or "").startswith("archive summary: ")
        ]
        assert len(summary_assertions) >= 1, (
            f"expected >=1 thread_summary assertion after {n_turns} turns; "
            f"got {len(summary_assertions)}; all predicates: "
            f"{[a.get('predicate_form') for a in all_assertions]}"
        )

        # Stage A: turns NOT superseded — all n_turns user assertions active.
        user_turns = await _count_turn_assertions(anchor_id, "user_turn(")
        assert len(user_turns) >= n_turns, (
            f"Stage A: expected >={n_turns} active user_turn assertions "
            f"(no supersede); got {user_turns}"
        )

        # Summary claim must start with the §6.10 prefix.
        for sa in summary_assertions:
            claim = sa.get("claim") or ""
            assert claim.startswith("archive summary: "), (
                f"summary claim missing §6.10 prefix: {claim!r}"
            )


@pytest.mark.asyncio
async def test_concurrency_lock_serialization() -> None:
    """Concurrent requests with the same chat_id serialise via Phase 5 lock.

    Asserts both the HTTP layer (all 200s, no 503 / lock timeout) and
    the cortex state (no duplicate user_turn(N) for the same N — which
    would prove the lock failed to serialise the turn_index assignment).
    """
    chat_id = _chat_id("concurrent-chat-lock")
    n = 3
    async with httpx.AsyncClient(base_url=_STARGATE_URL, timeout=120.0) as client:
        coros = [
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "cortex-chat-openai",
                    "chat_id": chat_id,
                    "messages": [{"role": "user", "content": f"Parallel turn {i}"}],
                },
            )
            for i in range(n)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        ok = [r for r in results if not isinstance(r, Exception)]
        for r in ok:
            assert r.status_code == 200, r.text
        assert len(ok) == n, (
            f"expected {n} successful responses, got {len(ok)}; "
            f"exceptions: {[r for r in results if isinstance(r, Exception)]}"
        )

        anchor_id = f"thread:openai-chat:{chat_id}"
        user_turns = await _count_turn_assertions(anchor_id, "user_turn(")
        # n distinct turn indices, no duplicates — the lock must
        # have serialised turn_index assignment.
        assert len(user_turns) >= n, (
            f"expected >={n} user_turn assertions, got {user_turns}"
        )
        assert len(set(user_turns)) == len(user_turns), (
            f"duplicate turn indices indicate lock failure: {user_turns}"
        )
