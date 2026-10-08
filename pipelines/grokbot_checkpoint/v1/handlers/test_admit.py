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
async def test_path_escape_and_replay() -> None:
    bad = await run_admit(
        {"mode": "seal", "house": "h", "n": 1, "envelope_ref": "../x"}, {}
    )
    assert bad["refused"]["code"] == "admit.path_escape"
    replay = await run_admit(
        {
            "mode": "seal",
            "house": "h",
            "n": 2,
            "_card_rows": [{"n": 2}],
            "_fact_exists": True,
        },
        {},
    )
    assert replay["replayed"] is True
