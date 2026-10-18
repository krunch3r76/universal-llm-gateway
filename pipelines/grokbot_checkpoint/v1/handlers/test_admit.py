"""Admit validation, path escape, replay, handler registration."""

from __future__ import annotations

import pytest

from . import register_handlers
from .admit import run_admit

pytestmark = pytest.mark.offline


class _Router:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def register_domain_handler_class(
        self, domain: str, step_type: str, cls: type
    ) -> None:
        assert cls.step_type == step_type
        self.rows.append((domain, step_type))


def test_register_handlers_covers_steps() -> None:
    router = _Router()
    register_handlers(router)  # type: ignore[arg-type]
    types = {step for _domain, step in router.rows}
    assert {domain for domain, _ in router.rows} == {"grokbot_checkpoint"}
    assert "grokbot_checkpoint_source_v1" in types
    assert "grokbot_checkpoint_verify_resume_v1" in types
    assert len(types) == 13


@pytest.mark.asyncio
async def test_path_escape_and_replay(fs) -> None:
    bad = await run_admit({"mode": "seal", "house": "h", "n": 1, "raw_ref": "../x"}, {})
    assert bad["refused"]["code"] == "admit.path_escape"
    card = (
        "## Checkpoint log\n\n| n | when | trigger | expected | envelope | pipeline |\n"
        "|---|---|---|---|---|---|\n| 2 | t | hop | sha | cell | pipeline e |\n"
    )
    fs.files["notes/card.md"] = card.encode()

    async def get(path, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns" and params and params.get("thread") == "v":
            return {
                "turns": [{"subject": "FACT|house-cp|h|2", "superseded": False}]
            }, 200
        return {"turns": []}, 200

    replay = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 2,
            "agent_id": "agent-replay",
            "card_uri": "notes/card.md",
            "verify_lane": "v",
        },
        {},
        fs=fs,
        get=get,
    )
    assert replay["replayed"] is True


@pytest.mark.asyncio
async def test_bool_n_refuses() -> None:
    out = await run_admit(
        {"mode": "seal", "house": "h", "n": True, "agent_id": "agent"},
        {},
    )
    assert out["refused"]["code"] == "admit.n"


@pytest.mark.asyncio
async def test_seal_mode_requires_agent_id() -> None:
    out = await run_admit({"mode": "seal", "house": "h", "n": 1}, {})
    assert out["refused"]["code"] == "admit.agent_id"


@pytest.mark.asyncio
async def test_seal_uuid_with_root_thread() -> None:
    bad = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 1,
            "root_thread": "15902",
            "agent_id": "NOT-A-UUID",
        },
        {},
    )
    assert (bad.get("refused") or {}).get("code") == "admit.agent_id_not_uuid"
    upper = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 1,
            "root_thread": "15902",
            "agent_id": "A1B2C3D4-E5F6-7890-ABCD-EF1234567890",
        },
        {},
    )
    assert (upper.get("refused") or {}).get("code") == "admit.agent_id_not_uuid"
    ok = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 1,
            "agent_id": "not-a-uuid",
        },
        {},
    )
    assert ok.get("refused") is None


@pytest.mark.asyncio
async def test_seal_uuid_canonical() -> None:
    agent = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    out = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 1,
            "root_thread": "15902",
            "agent_id": agent,
        },
        {},
    )
    assert out.get("refused") is None
    assert out.get("agent_id") == agent
    padded = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 1,
            "root_thread": "15902",
            "agent_id": f"  {agent}  ",
        },
        {},
    )
    assert padded.get("refused") is None
    assert padded["agent_id"] == agent


@pytest.mark.asyncio
async def test_admit_refuses_blank_next_acts() -> None:
    letter = {
        "author": "grok-bot-hm1",
        "mission": "resume the house",
        "state": "open",
        "next_acts": "",
        "load_bearing": ["intent is not in the transcript"],
    }
    out = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 1,
            "agent_id": "11111111-1111-4111-8111-111111111111",
            "handoff": letter,
        },
        {},
    )
    assert out["refused"]["code"] == "admit.handoff_invalid"
    assert out["refused"]["message"] == "next_acts"
