"""Fork 15 continuity-hop proof: one row per input class.

Breaks when the hop builder posts a refused job, omits a legal job, or
reports ``absent`` for a refusal. The poster is the commission boundary:
a refusal returns before it runs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tools.agent_bus.hop import (
    HOP_JOB_ABSENT,
    commission_continuity_hop,
    hop_generate_payload,
    resolve_continuity_hop_job,
)

_INLINE_LEGAL = (
    "freeform",
    "mechanical",
    "code-review",
    "delivery-review",
    "check-review",
    "confer",
    "investigate",
)
_NOT_ADMITTED = ("answer", "ask", "verify", "execute", "propagate", "seed", "recon")
_SOURCE_REF = ("implement", "sketch", "wrap", "conductor")
_UNKNOWN = ("none", "pure-mechanical", "consult", "not-a-job")


async def _run(decision: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    posts: list[dict[str, Any]] = []

    async def poster(payload: dict[str, Any]) -> dict[str, Any]:
        posts.append(payload)
        return {"status_code": 200, "execution_id": "ex-hop"}

    payload = hop_generate_payload(
        model="cdp/opus-5.5-high",
        prompt="TYPE: CONTINUITY_HANDOFF",
        thread_id="77",
        from_agent="web-anthropic",
        decision=decision,
    )
    result = await commission_continuity_hop(payload, decision, poster)
    return result, posts


def test_continuity_hop_six_input_classes() -> None:
    """Six rows. A wrong reason, a stray POST, or a wrong report job fails."""

    async def missing() -> None:
        decision = resolve_continuity_hop_job(contract_line=None)
        result, posts = await _run(decision)
        body = result["json"]
        assert body["session"] == "operator-proxy"
        assert body["mission_kind"] == "hop"
        assert "job" not in body
        assert result["status_code"] == 200
        assert result["execution_id"] == "ex-hop"
        assert result["report"]["job"] == HOP_JOB_ABSENT
        assert len(posts) == 1

    async def inline_legal() -> None:
        for job_id in _INLINE_LEGAL:
            decision = resolve_continuity_hop_job(contract_line=job_id)
            result, posts = await _run(decision)
            assert result["json"]["job"] == job_id
            assert result["status_code"] == 200
            assert result["execution_id"] == "ex-hop"
            assert result["report"]["job"] == job_id
            assert posts[-1]["job"] == job_id

    async def generate_refuses() -> None:
        for job_id in _NOT_ADMITTED:
            decision = resolve_continuity_hop_job(contract_line=job_id)
            result, posts = await _run(decision)
            assert result["posted"] is False
            assert result["reason"] == "job_unknown"
            assert result["report"]["job"] != HOP_JOB_ABSENT
            assert posts == []

    async def source_ref_only() -> None:
        for job_id in _SOURCE_REF:
            decision = resolve_continuity_hop_job(contract_line=job_id)
            result, posts = await _run(decision)
            assert result["posted"] is False
            assert result["reason"] == "handle_forbidden"
            assert result["report"]["job"] != HOP_JOB_ABSENT
            assert result["report"]["job"] != job_id
            assert posts == []

    async def unknown() -> None:
        for token in _UNKNOWN:
            decision = resolve_continuity_hop_job(contract_line=token)
            result, posts = await _run(decision)
            assert result["posted"] is False
            assert result["reason"] == "job_unknown"
            assert result["report"]["job"] != HOP_JOB_ABSENT
            assert posts == []

    async def explicit_freeform() -> None:
        decision = resolve_continuity_hop_job(
            contract_line="implement",
            explicit_job="freeform",
        )
        result, posts = await _run(decision)
        assert result["json"]["job"] == "freeform"
        assert result["status_code"] == 200
        assert result["execution_id"] == "ex-hop"
        assert result["report"]["job"] == "freeform"
        assert len(posts) == 1

    asyncio.run(missing())
    asyncio.run(inline_legal())
    asyncio.run(generate_refuses())
    asyncio.run(source_ref_only())
    asyncio.run(unknown())
    asyncio.run(explicit_freeform())
