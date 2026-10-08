"""Admit validation, path escape, replay, handler registration."""

from __future__ import annotations

import pytest

from . import register_handlers
from .admit import run_admit

pytestmark = pytest.mark.offline


class _Router:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def register_domain_handler_class(self, domain: str, step_type: str, cls: type) -> None:
        assert cls.step_type == step_type
        self.rows.append((domain, step_type))


def test_register_handlers_covers_steps() -> None:
    router = _Router()
    register_handlers(router)  # type: ignore[arg-type]
    types = {step for _domain, step in router.rows}
    assert {domain for domain, _ in router.rows} == {"grokbot_checkpoint"}
    assert "grokbot_checkpoint_source_v1" in types
    assert "grokbot_checkpoint_verify_resume_v1" in types
    assert len(types) == 10


@pytest.mark.asyncio
async def test_path_escape_and_replay(fs) -> None:
    bad = await run_admit(
        {"mode": "seal", "house": "h", "n": 1, "raw_ref": "../x"}, {}
    )
    assert bad["refused"]["code"] == "admit.path_escape"
    card = (
        "## Checkpoint log\n\n| n | when | trigger | expected | envelope | pipeline |\n"
        "|---|---|---|---|---|---|\n| 2 | t | hop | sha | cell | pipeline e |\n"
    )
    fs.files["notes/card.md"] = card.encode()

    async def get(path, params=None):
        return {"turns": [{"subject": "FACT|house-cp|h|2", "superseded": False}]}, 200

    replay = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 2,
            "card_uri": "notes/card.md",
            "verify_lane": "v",
        },
        {},
        fs=fs,
        get=get,
    )
    assert replay["replayed"] is True
