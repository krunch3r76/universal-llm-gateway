"""Cloud proxy anthropic credit event publishing."""

from __future__ import annotations

import asyncio
import dataclasses
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import (
    Admit,
    CreditPolicy,
    emit,
    set_event_emitter,
    set_policy,
)
from universal_event_bus import EventBus

from services.universal_cloud_proxy.cloud_proxy import (
    _CREDIT_EVENT_FACTORIES,
    make_credit_emitter,
)
from services.universal_cloud_proxy.events import AnthropicCreditStreamFloored

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_usage_partial_event_reaches_event_bus(tmp_path: Path) -> None:
    published: list[str] = []
    bus = EventBus()

    async def _capture_publish(event: object) -> None:
        published.append(getattr(event, "signal", ""))

    bus.publish = _capture_publish  # type: ignore[method-assign]

    set_event_emitter(make_credit_emitter(bus))
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


@pytest.mark.asyncio
async def test_overrun_event_reaches_event_bus(tmp_path: Path) -> None:
    published: list[str] = []
    bus = EventBus()

    async def _capture_publish(event: object) -> None:
        published.append(getattr(event, "signal", ""))

    bus.publish = _capture_publish  # type: ignore[method-assign]

    set_event_emitter(make_credit_emitter(bus))
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
            "overrun-ev",
            "anthropic/claude-sonnet-5",
            {
                "model": "claude-sonnet-5",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "x"}],
            },
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    st = policy.status()
    reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
    reservation = reserve["usd"]
    policy.commit(
        "overrun-ev",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 500_000,
        },
        now=t0,
    )
    await asyncio.sleep(0.05)
    assert "anthropic.credit.overrun" in published
    assert Decimal(reservation) > Decimal("0")
    set_event_emitter(None)
    set_policy(None)


def test_stream_floored_factory_registered() -> None:
    """T6/T7: stream.floored factory is wired and signal matches universal events regex."""
    assert "anthropic.credit.stream.floored" in _CREDIT_EVENT_FACTORIES
    event = AnthropicCreditStreamFloored(request_id="x", reason="unknown_dict_usage")
    assert event.signal == "anthropic.credit.stream.floored"
    assert re.fullmatch(r"^[a-z]+(\.[a-z]+){1,4}$", event.signal)


@pytest.mark.asyncio
async def test_stream_floored_event_reaches_event_bus(tmp_path: Path) -> None:
    """T6: make_credit_emitter publishes anthropic.credit.stream.floored."""
    published: list[str] = []
    bus = EventBus()

    async def _capture_publish(event: object) -> None:
        published.append(getattr(event, "signal", ""))

    bus.publish = _capture_publish  # type: ignore[method-assign]

    set_event_emitter(make_credit_emitter(bus))
    emit(
        "anthropic.credit.stream.floored",
        {
            "request_id": "stream-floor-ev",
            "model": None,
            "reason": "stream_abnormal_end",
            "unknown_usage_keys": [],
        },
    )
    await asyncio.sleep(0.05)
    assert "anthropic.credit.stream.floored" in published
    set_event_emitter(None)
