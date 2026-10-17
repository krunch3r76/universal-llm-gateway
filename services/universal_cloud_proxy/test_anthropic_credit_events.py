"""Cloud proxy anthropic credit event publishing."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy, set_event_emitter, set_policy

from services.universal_cloud_proxy.events import AnthropicCreditUsagePartial

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_usage_partial_event_reaches_event_bus(tmp_path: Path) -> None:
    published: list[str] = []

    class _Bus:
        async def publish(self, event: object) -> None:
            published.append(getattr(event, "signal", ""))

    bus = _Bus()

    def _emit(name: str, payload: dict) -> None:
        if name == "anthropic.credit.usage_partial":
            loop = asyncio.get_event_loop()
            loop.create_task(bus.publish(AnthropicCreditUsagePartial(**payload)))

    set_event_emitter(_emit)
    cfg = dataclasses.replace(
        load_credit_config(),
        ledger_path=tmp_path / "ledger.sqlite",
    )
    init_ledger(cfg)
    policy = CreditPolicy(cfg)
    set_policy(policy)
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "partial-ev",
            "anthropic/claude-sonnet-5",
            {
                "model": "claude-sonnet-5",
                "max_tokens": 100_000,
                "messages": [{"role": "user", "content": "x"}],
            },
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    policy.commit("partial-ev", {"input_tokens": 100}, now=t0)
    await asyncio.sleep(0.05)
    assert "anthropic.credit.usage_partial" in published
    set_event_emitter(None)
    set_policy(None)
