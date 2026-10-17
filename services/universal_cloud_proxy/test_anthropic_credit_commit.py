"""A9 commit-path tests — raw Anthropic usage before OpenAI translation."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy, get_policy, set_policy

from services.universal_cloud_proxy.adapters.anthropic import AnthropicAdapter
from services.universal_cloud_proxy.config import ProviderConfig

pytestmark = pytest.mark.offline

_FIXTURES = Path(__file__).parent / "fixtures" / "anthropic_usage"


def _load_json(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _load_sse(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _make_adapter(
    upstream_payload: dict | str,
    *,
    stream: bool = False,
) -> tuple[AnthropicAdapter, dict[str, int]]:
    counts = {"messages": 0, "count_tokens": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/count_tokens"):
            counts["count_tokens"] += 1
            return httpx.Response(200, json={"input_tokens": 1000})
        if path.endswith("/messages"):
            counts["messages"] += 1
            if stream:
                sse = upstream_payload if isinstance(upstream_payload, str) else ""
                return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
            assert isinstance(upstream_payload, dict)
            return httpx.Response(200, json=upstream_payload)
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        provider="anthropic",
        api_key="test-key",
        base_url="https://api.example.com/v1",
    )
    return AnthropicAdapter(config=config, client=client), counts


def _committed_usd_for_request(request_id: str) -> Decimal:
    st = get_policy().status()
    entries = st.get("entries") or st.get("last_entries") or []
    commit = next(
        e
        for e in entries
        if e.get("kind") == "commit" and e.get("request_id") == request_id
    )
    return Decimal(str(commit["usd"]))


def _committed_usd_for_sole_ledger_commit() -> Decimal:
    st = get_policy().status()
    entries = st.get("entries") or st.get("last_entries") or []
    commits = [e for e in entries if e.get("kind") == "commit"]
    assert len(commits) == 1, f"expected one commit, got {len(commits)}"
    return _committed_usd_for_request(str(commits[0]["request_id"]))


@pytest.mark.asyncio
async def test_non_stream_full_breakdown_exact_usd() -> None:
    payload = _load_json("non_stream_full_breakdown.json")
    adapter, _counts = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.00717")


@pytest.mark.asyncio
async def test_non_stream_no_breakdown_prices_cache_creation_at_2x() -> None:
    payload = _load_json("non_stream_no_breakdown.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.005")


@pytest.mark.asyncio
async def test_stream_message_start_and_delta_exact_usd() -> None:
    sse = _load_sse("stream_full_usage.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    chunks: list[bytes] = []
    async for chunk in adapter.forward_native_stream(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ):
        chunks.append(chunk)
    assert chunks
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.00717")


@pytest.mark.asyncio
async def test_stream_delta_overrides_start_usage_no_double_count() -> None:
    sse = _load_sse("stream_delta_overrides_input.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    async for _chunk in adapter.forward_native_stream(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ):
        pass
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.00737")


@pytest.mark.asyncio
async def test_stream_truncated_before_delta_commits_full_reservation() -> None:
    """Break covered: stream ends without message_delta usage → commit_full reservation."""
    from anthropic_credit.policy import admit

    from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
        StreamUsageTap,
        finalize_stream_credit,
    )

    request_id = "stream-truncated-req"
    upstream_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    reservation = admit(
        request_id,
        "anthropic/claude-sonnet-5",
        upstream_body,
        token_count=1000,
    )
    assert isinstance(reservation, Admit)

    tap = StreamUsageTap()
    for line in _load_sse("stream_truncated_before_delta.sse").splitlines():
        tap.feed_line(line)

    assert tap.delta_usage_seen is False
    finalize_stream_credit(request_id, tap, normal_end=True)

    st = get_policy().status()
    entries = st.get("entries") or []
    commits = [e for e in entries if e.get("kind") == "commit"]
    assert commits, f"expected commit entry, got {entries!r}"
    last = commits[-1]
    assert last["kind"] == "commit"
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    assert Decimal(str(last["usd"])) == Decimal(str(reserve["usd"]))


@pytest.mark.asyncio
async def test_non_stream_missing_usage_commits_full_reservation() -> None:
    payload = _load_json("non_stream_missing_usage.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    st = get_policy().status()
    entries = st.get("entries") or []
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    commit = next(e for e in entries if e.get("kind") == "commit")
    assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))


@pytest.mark.asyncio
async def test_haiku_long_tier_cache_read_only(tmp_path: Path) -> None:
    rates_path = tmp_path / "rates.yaml"
    rates_path.write_text(
        """
models:
  - model_id: anthropic/claude-haiku-5-5
    input_rate_per_m: 0.10
    output_rate_per_m: 0.50
    cache_write_rate_per_m: 0.125
    cache_read_rate_per_m: 0.01
    long_prompt_threshold_tokens: 100000
    long_input_rate_per_m: 0.50
    long_cache_write_rate_per_m: 0.625
    long_cache_read_rate_per_m: 0.05
    long_output_rate_per_m: 2.50
    source: test
    pinned: true
""",
        encoding="utf-8",
    )
    base = load_credit_config()
    cfg = dataclasses.replace(
        base,
        ledger_path=tmp_path / "ledger.sqlite",
        metered_models=[
            *base.metered_models,
            "anthropic/claude-haiku-5-5",
        ],
    )
    init_ledger(cfg)
    set_policy(CreditPolicy(cfg, rates_path=rates_path))

    payload = _load_json("non_stream_haiku_long_tier.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-haiku-5-5",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.0115")


def _partial_stream_sse() -> str:
    return (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":{'
        '"input_tokens":1000,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":0}}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n\n'
    )


@pytest.mark.parametrize(
    "method_name",
    ["forward_chat_stream", "forward_native_stream"],
)
@pytest.mark.asyncio
async def test_stream_aclose_mid_stream_one_floor_commit(method_name: str) -> None:
    adapter, _ = _make_adapter(_partial_stream_sse(), stream=True)
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    gen = getattr(adapter, method_name)(body)
    async for _chunk in gen:
        await gen.aclose()
        break
    st = get_policy().status()
    entries = st.get("entries") or []
    commits = [e for e in entries if e.get("kind") == "commit"]
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    assert len(commits) == 1
    assert Decimal(str(commits[0]["usd"])) == Decimal(str(reserve["usd"]))


def _make_blocking_stream_adapter(
    first_sse: str,
    block: asyncio.Event,
) -> tuple[AnthropicAdapter, dict[str, int]]:
    counts = {"messages": 0, "count_tokens": 0}
    first_bytes = first_sse.encode("utf-8")

    class _BlockStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self._sent_first = False

        async def __aiter__(self):
            if not self._sent_first:
                self._sent_first = True
                yield first_bytes
            await block.wait()
            if False:  # pragma: no cover — keep async generator form
                yield b""

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/count_tokens"):
            counts["count_tokens"] += 1
            return httpx.Response(200, json={"input_tokens": 1000})
        if path.endswith("/messages"):
            counts["messages"] += 1
            return httpx.Response(
                200,
                stream=_BlockStream(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        provider="anthropic",
        api_key="test-key",
        base_url="https://api.example.com/v1",
    )
    return AnthropicAdapter(config=config, client=client), counts


@pytest.mark.parametrize(
    "method_name",
    ["forward_chat_stream", "forward_native_stream"],
)
@pytest.mark.asyncio
async def test_stream_task_cancel_mid_stream_one_floor_commit(method_name: str) -> None:
    block = asyncio.Event()
    sse = _load_sse("stream_web_search_usage.sse")
    parts = [part for part in sse.split("\n\n") if part.strip()]
    head_sse = parts[0] + "\n\n" + parts[1] + "\n\n"
    adapter, _ = _make_blocking_stream_adapter(head_sse, block)
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    first_chunk = asyncio.Event()

    async def _consume() -> None:
        async for _chunk in getattr(adapter, method_name)(body):
            if not first_chunk.is_set():
                first_chunk.set()

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(first_chunk.wait(), timeout=2.0)
    await asyncio.sleep(0.05)
    for _ in range(50):
        if get_policy().status().get("entries"):
            break
        await asyncio.sleep(0.01)
    entries = get_policy().status().get("entries") or []
    assert any(e.get("kind") == "reserve" for e in entries)
    assert not any(e.get("kind") == "commit" for e in entries)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    entries = get_policy().status().get("entries") or []
    commits = [e for e in entries if e.get("kind") == "commit"]
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    assert len(commits) == 1
    tapped = Decimal("0.107")
    reservation = Decimal(str(reserve["usd"]))
    assert tapped > reservation
    assert Decimal(str(commits[0]["usd"])) == max(tapped, reservation)


@pytest.mark.parametrize(
    "method_name",
    ["forward_chat_stream", "forward_native_stream"],
)
@pytest.mark.asyncio
async def test_stream_web_search_usage_commits_token_plus_fee(method_name: str) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shipped_rates = repo_root / "config" / "model_rates.yaml"
    prior = get_policy()
    set_policy(
        CreditPolicy(
            prior._cfg,
            ledger=prior._ledger,
            rates_path=shipped_rates,
        )
    )
    sse = _load_sse("stream_web_search_usage.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    async for _chunk in getattr(adapter, method_name)(body):
        pass
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.107")


@pytest.mark.parametrize(
    "method_name",
    ["forward_chat_stream", "forward_native_stream"],
)
@pytest.mark.asyncio
async def test_stream_server_tool_mixed_types_floors_to_reservation(
    method_name: str,
) -> None:
    sse = _load_sse("stream_server_tool_mixed_types.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 20000,
        "messages": [{"role": "user", "content": "hi"}],
    }
    async for _chunk in getattr(adapter, method_name)(body):
        pass
    st = get_policy().status()
    entries = st.get("entries") or []
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    commit = next(e for e in entries if e.get("kind") == "commit")
    computed = Decimal("0.107000")
    reservation = Decimal(str(reserve["usd"]))
    assert reservation > computed
    assert Decimal(str(commit["usd"])) == reservation
    assert Decimal(str(commit["usd"])) != computed


@pytest.mark.asyncio
async def test_non_stream_server_tool_mixed_types_floors_to_reservation() -> None:
    payload = _load_json("non_stream_server_tool_mixed_types.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 20000,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    st = get_policy().status()
    entries = st.get("entries") or []
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    commit = next(e for e in entries if e.get("kind") == "commit")
    computed = Decimal("0.107")
    reservation = Decimal(str(reserve["usd"]))
    assert reservation > computed
    assert Decimal(str(commit["usd"])) == reservation
    assert Decimal(str(commit["usd"])) != computed


@pytest.mark.asyncio
async def test_stream_top_level_dict_usage_key_floors_commit() -> None:
    sse = (
        'event: message_start\n'
        'data: {"type":"message_start","message":{"usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":50}}}\n\n'
        'event: message_delta\n'
        'data: {"type":"message_delta","usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":50,'
        '"opaque_usage":{"nested":1}}}\n\n'
        'event: message_stop\n'
        'data: {"type":"message_stop"}\n\n'
    )
    adapter, _ = _make_adapter(sse, stream=True)
    async for _chunk in adapter.forward_native_stream(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ):
        pass
    st = get_policy().status()
    reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
    commit = next(e for e in st["entries"] if e["kind"] == "commit")
    reservation = Decimal(str(reserve["usd"]))
    computed = Decimal("0.000700")
    assert Decimal(str(commit["usd"])) == max(computed, reservation)


@pytest.mark.parametrize(
    "method_name",
    ["forward_chat_stream", "forward_native_stream"],
)
@pytest.mark.asyncio
async def test_stream_unknown_server_tool_commits_max_reservation(
    method_name: str,
) -> None:
    sse = _load_sse("stream_unknown_server_tool.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    async for _chunk in getattr(adapter, method_name)(body):
        pass
    st = get_policy().status()
    entries = st.get("entries") or []
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    commit = next(e for e in entries if e.get("kind") == "commit")
    computed = Decimal("0.000000")
    reservation = Decimal(str(reserve["usd"]))
    assert Decimal(str(commit["usd"])) == max(computed, reservation)


@pytest.mark.asyncio
async def test_forward_chat_stream_normal_end_commits_tapped_usage() -> None:
    sse = _load_sse("stream_full_usage.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    async for _chunk in adapter.forward_chat_stream(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ):
        pass
    assert _committed_usd_for_sole_ledger_commit() == Decimal("0.00717")


def _stream_tap_from_lines(*lines: str):
    from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
        StreamUsageTap,
    )

    tap = StreamUsageTap()
    for line in lines:
        tap.feed_line(line)
    return tap


def _admit_claude_sonnet_5(request_id: str) -> Admit:
    from anthropic_credit.policy import admit

    result = admit(
        request_id,
        "anthropic/claude-sonnet-5",
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        },
        token_count=1000,
    )
    assert isinstance(result, Admit)
    return result


def test_stream_output_tokens_details_exact_commit_no_floored_emit() -> None:
    """T1: output_tokens_details on final delta must not floor a normal stream."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t1-output-details"
        reservation = _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":1,'
            '"cache_creation":{"ephemeral_5m_input_tokens":0}}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":0}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        entries = st.get("entries") or []
        reserve = next(e for e in entries if e.get("kind") == "reserve")
        last_commit = next(e for e in entries if e.get("kind") == "commit")
        computed = Decimal("0.00024")
        assert Decimal(str(last_commit["usd"])) == computed
        assert Decimal(str(last_commit["usd"])) != Decimal(str(reserve["usd"]))
        assert Decimal(str(last_commit["usd"])) != reservation.reservation_usd
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)


def test_stream_unknown_dict_usage_key_floors_and_emits() -> None:
    """T2: unknown dict keys still fail-closed with stream.floored."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t2-foo-details"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"foo_details":{}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
        assert len(floored) == 1
        assert floored[0]["reason"] == "unknown_dict_usage"
        assert floored[0]["unknown_usage_keys"] == ["foo_details"]
    finally:
        set_event_emitter(None)


def test_stream_output_details_subcount_over_output_tokens_floors() -> None:
    """T3: thinking_tokens above billing output_tokens must floor."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t3-over-subcount"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":5}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
        assert len(floored) == 1
        assert floored[0]["reason"] == "output_details_undercount_risk"
    finally:
        set_event_emitter(None)


@pytest.mark.parametrize(
    ("details_fragment", "request_id"),
    [
        ('"output_tokens_details":{"thinking_tokens":"3"}', "t3b-thinking-str"),
        ('"output_tokens_details":{"thinking_tokens":true}', "t3b-thinking-bool"),
        ('"output_tokens_details":{"thinking_tokens":-1}', "t3b-thinking-neg"),
        ('"output_tokens_details":{"thinking_tokens":null}', "t3b-thinking-null"),
        ('"output_tokens_details":[1]', "t3b-details-list"),
    ],
)
def test_stream_output_details_invalid_subvalues_floor(
    details_fragment: str, request_id: str
) -> None:
    """T3b: non-int or non-dict output_tokens_details must floor."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        _admit_claude_sonnet_5(request_id)
        delta_data = (
            '{"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            + details_fragment
            + "}}"
        )
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            "data: " + delta_data,
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
        assert len(floored) == 1
        assert floored[0]["reason"] == "output_details_undercount_risk"
    finally:
        set_event_emitter(None)


def test_non_stream_output_tokens_details_parity_with_plain_usage() -> None:
    """T4: non-stream path ignores output_tokens_details (unchanged F3)."""
    from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
        finalize_non_stream_credit,
    )

    base_usage = {
        "input_tokens": 100,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 4,
    }
    expected = Decimal("0.00024")

    plain_rid = "t4-plain"
    details_rid = "t4-details"
    _admit_claude_sonnet_5(plain_rid)
    resp_plain = httpx.Response(200, json={"id": "m1", "usage": base_usage})
    finalize_non_stream_credit(plain_rid, response=resp_plain)
    plain_commit = _committed_usd_for_request(plain_rid)

    _admit_claude_sonnet_5(details_rid)
    usage_with = {
        **base_usage,
        "output_tokens_details": {"thinking_tokens": 3},
    }
    resp_details = httpx.Response(200, json={"id": "m2", "usage": usage_with})
    finalize_non_stream_credit(details_rid, response=resp_details)
    details_commit = _committed_usd_for_request(details_rid)

    assert plain_commit == expected
    assert details_commit == expected
    assert details_commit == plain_commit == expected


def test_stream_details_first_key_on_final_delta_exact_commit() -> None:
    """Frame order: output_tokens_details first in final message_delta usage dict."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t-frame-final-details-first"
        _admit_claude_sonnet_5(request_id)
        delta_data = (
            '{"type":"message_delta","usage":{'
            '"output_tokens_details":{"thinking_tokens":3},'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}'
        )
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":1}}}',
            "",
            "event: message_delta",
            "data: " + delta_data,
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        assert _committed_usd_for_request(request_id) == Decimal("0.00024")
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)


def test_stream_details_on_early_delta_final_output_tokens_exact_commit() -> None:
    """Frame order: details on earlier delta, billing output_tokens on later delta."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t-frame-split-deltas"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":1}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":1,'
            '"output_tokens_details":{"thinking_tokens":3}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        assert _committed_usd_for_request(request_id) == Decimal("0.00024")
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)


def test_stream_floored_no_emit_when_commit_raises() -> None:
    """Emit runs only after commit; commit failure must not publish stream.floored."""
    from anthropic_credit.ledger import LedgerUnavailable
    from anthropic_credit.policy import release, set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t-commit-fail-no-emit"
        _admit_claude_sonnet_5(request_id)
        release(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"foo_details":{}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        with pytest.raises(LedgerUnavailable):
            finalize_stream_credit(request_id, tap, normal_end=True)
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)


def _stream_lines_message_start_output_1_final_delta(
    *,
    output_tokens_field: str = '"output_tokens":4',
) -> list[str]:
    delta_inner = (
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,'
        + output_tokens_field
    )
    return [
        "event: message_start",
        'data: {"type":"message_start","message":{"model":"claude-sonnet-5","usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":1}}}',
        "",
        "event: message_delta",
        'data: {"type":"message_delta","usage":{' + delta_inner + "}}",
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]


def _finalize_tap_with_emit_capture(
    request_id: str, lines: list[str]
) -> tuple[list[tuple[str, dict]], Decimal, Decimal]:
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(*lines)
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        return (
            emitted,
            Decimal(str(reserve["usd"])),
            Decimal(str(commit["usd"])),
        )
    finally:
        set_event_emitter(None)


@pytest.mark.parametrize(
    ("bad_output_tokens", "request_id"),
    [
        ('"output_tokens":"4"', "p5a-output-str"),
        ('"output_tokens":null', "p5a-output-null"),
        ('"output_tokens":true', "p5a-output-bool"),
    ],
)
def test_stream_bad_final_output_tokens_floors_invalid_usage(
    bad_output_tokens: str, request_id: str
) -> None:
    """P5-a: non-int final output_tokens must not undercount message_start value."""
    lines = _stream_lines_message_start_output_1_final_delta(
        output_tokens_field=bad_output_tokens,
    )
    emitted, reservation, commit_usd = _finalize_tap_with_emit_capture(request_id, lines)
    assert commit_usd == reservation
    assert commit_usd != Decimal("0.00024")
    floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
    assert len(floored) == 1
    assert floored[0]["reason"] == "invalid_usage_value"
    assert floored[0]["invalid_usage_keys"] == ["output_tokens"]


def test_stream_bad_input_tokens_on_start_floors() -> None:
    """P5-b: bool input_tokens on message_start floors with invalid_usage_keys."""
    lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{'
        '"input_tokens":true,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":1}}}',
        "",
        "event: message_delta",
        'data: {"type":"message_delta","usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":4}}',
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]
    emitted, reservation, commit_usd = _finalize_tap_with_emit_capture(
        "p5b-input-bool", lines
    )
    assert commit_usd == reservation
    floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
    assert floored[0]["reason"] == "invalid_usage_value"
    assert floored[0]["invalid_usage_keys"] == ["input_tokens"]


@pytest.mark.parametrize(
    ("cache_fragment", "request_id"),
    [
        ('"cache_creation":{"ephemeral_5m_input_tokens":"3"}', "p5c-cache-sub-str"),
        ('"cache_creation":{"ephemeral_5m_input_tokens":null}', "p5c-cache-sub-null"),
        ('"cache_creation":{"ephemeral_5m_input_tokens":true}', "p5c-cache-sub-bool"),
        ('"cache_creation":null', "p5c-cache-top-null"),
    ],
)
def test_stream_bad_cache_creation_floors(
    cache_fragment: str, request_id: str
) -> None:
    """P5-c: invalid cache_creation sub-values or top-level null floor."""
    lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":4,'
        + cache_fragment
        + "}}}",
        "",
        "event: message_delta",
        'data: {"type":"message_delta","usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":4}}',
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]
    emitted, reservation, commit_usd = _finalize_tap_with_emit_capture(request_id, lines)
    assert commit_usd == reservation
    floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
    assert floored[0]["reason"] == "invalid_usage_value"
    assert floored[0]["invalid_usage_keys"] == ["cache_creation"]


@pytest.mark.parametrize(
    ("server_tool_fragment", "request_id"),
    [
        ('"server_tool_use":{"web_search_requests":true}', "p5d-tool-bool"),
        ('"server_tool_use":null', "p5d-tool-null"),
    ],
)
def test_stream_bad_server_tool_use_floors(
    server_tool_fragment: str, request_id: str
) -> None:
    """P5-d: bool server_tool_use sub-value or null top-level must floor."""
    lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":4}}}',
        "",
        "event: message_delta",
        'data: {"type":"message_delta","usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":4,'
        + server_tool_fragment
        + "}}",
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]
    emitted, reservation, commit_usd = _finalize_tap_with_emit_capture(request_id, lines)
    assert commit_usd == reservation
    floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
    assert len(floored) == 1
    if server_tool_fragment.endswith("null"):
        assert floored[0]["reason"] == "invalid_usage_value"
        assert floored[0]["invalid_usage_keys"] == ["server_tool_use"]
    else:
        assert floored[0]["reason"] == "unknown_dict_usage"


def test_stream_non_priced_scalars_exact_commit_no_emit() -> None:
    """P5-e: service_tier and other non-priced scalars must not floor valid priced usage."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "p5e-non-priced"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":1,'
            '"service_tier":"standard","inference_geo":"global","speed":"standard",'
            '"some_flag":true}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":0}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        assert _committed_usd_for_request(request_id) == Decimal("0.00024")
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)


@pytest.mark.parametrize(
    "bad_output",
    [
        {"output_tokens": "4"},
        {"output_tokens": None},
        {"output_tokens": True},
    ],
    ids=["str", "null", "bool"],
)
def test_non_stream_bad_output_tokens_floors_to_reservation(
    bad_output: dict,
) -> None:
    """P5-f: non-stream path already fail-closed on bad output_tokens."""
    from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
        finalize_non_stream_credit,
    )

    request_id = "p5f-non-stream-" + str(bad_output["output_tokens"])
    _admit_claude_sonnet_5(request_id)
    usage = {
        "input_tokens": 100,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        **bad_output,
    }
    resp = httpx.Response(200, json={"id": "m", "usage": usage})
    finalize_non_stream_credit(request_id, response=resp)
    st = get_policy().status()
    reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
    commit = next(e for e in st["entries"] if e["kind"] == "commit")
    assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))


def test_stream_output_details_sum_over_output_tokens_floors() -> None:
    """P2: sum of detail sub-counts above output_tokens must floor."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "p2-sum-over"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":3,"other":2}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
        assert floored[0]["reason"] == "output_details_undercount_risk"
        assert floored[0]["output_details"] == {"thinking_tokens": 3, "other": 2}
    finally:
        set_event_emitter(None)


def test_stream_output_details_sum_known_keys_monkeypatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2: with both keys known, sum 5 > 4 floors; sum 4 does not."""
    import services.universal_cloud_proxy.adapters.anthropic_credit_gate as gate_mod

    monkeypatch.setattr(
        gate_mod,
        "_KNOWN_OUTPUT_DETAIL_KEYS",
        frozenset({"thinking_tokens", "other"}),
    )
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "p2-sum-known-ok"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":2,"other":2}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        assert _committed_usd_for_request(request_id) == Decimal("0.00024")
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)

    emitted2: list[tuple[str, dict]] = []

    def _capture2(name: str, payload: dict) -> None:
        emitted2.append((name, dict(payload)))

    set_event_emitter(_capture2)
    try:
        request_id2 = "p2-sum-known-floor"
        _admit_claude_sonnet_5(request_id2)
        tap2 = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":3,"other":2}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        finalize_stream_credit(request_id2, tap2, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted2 if n == "anthropic.credit.stream.floored"]
        assert floored[0]["reason"] == "output_details_undercount_risk"
    finally:
        set_event_emitter(None)


def test_stream_unknown_output_detail_subkey_floors() -> None:
    """P3: future_tokens sub-key floors even when counts fit output_tokens."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "p3-future-subkey"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"output_tokens_details":{"thinking_tokens":0,"future_tokens":1}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
        assert floored[0]["reason"] == "output_details_undercount_risk"
        assert floored[0]["output_details"]["future_tokens"] == 1
    finally:
        set_event_emitter(None)


def test_stream_floored_event_full_payload_and_model() -> None:
    """NIT 4 / P1: stream.floored carries reasons, usage fields, and meter model."""
    lines = _stream_lines_message_start_output_1_final_delta(
        output_tokens_field='"output_tokens":"4"',
    )
    emitted, _, _ = _finalize_tap_with_emit_capture("nit4-full-payload", lines)
    floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
    assert len(floored) == 1
    p = floored[0]
    assert p["reasons"] == ["invalid_usage_value"]
    assert p["invalid_usage_keys"] == ["output_tokens"]
    assert p["output_tokens"] == 1
    assert p["output_details"] == {}
    assert p["model"] == "anthropic/claude-sonnet-5"


def test_stream_floored_model_none_without_message_start_model() -> None:
    """P1: missing message_start model yields model=None in event."""
    lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":1}}}',
        "",
        "event: message_delta",
        'data: {"type":"message_delta","usage":{'
        '"input_tokens":100,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":0,"output_tokens":"4"}}',
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]
    emitted, _, _ = _finalize_tap_with_emit_capture("nit4-no-model", lines)
    floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
    assert floored[0]["model"] is None


def test_stream_floored_no_emit_when_commit_raises_fallthrough() -> None:
    """Fallthrough branch: emit after commit; release then normal_end=False."""
    from anthropic_credit.ledger import LedgerUnavailable
    from anthropic_credit.policy import release, set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t-commit-fail-fallthrough"
        _admit_claude_sonnet_5(request_id)
        release(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        with pytest.raises(LedgerUnavailable):
            finalize_stream_credit(request_id, tap, normal_end=False)
        assert not any(name == "anthropic.credit.stream.floored" for name, _ in emitted)
    finally:
        set_event_emitter(None)


def test_stream_abnormal_end_floors_and_emits() -> None:
    """T5: fallthrough path emits stream_abnormal_end after floor commit."""
    from anthropic_credit.policy import set_event_emitter

    emitted: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict) -> None:
        emitted.append((name, dict(payload)))

    set_event_emitter(_capture)
    try:
        request_id = "t5-abnormal"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=False)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
        floored = [p for n, p in emitted if n == "anthropic.credit.stream.floored"]
        assert len(floored) == 1
        assert floored[0]["reason"] == "stream_abnormal_end"
    finally:
        set_event_emitter(None)


def test_stream_floored_emit_failure_still_commits_floor() -> None:
    """T8: emit errors must not block reservation floor commit."""
    from anthropic_credit.policy import set_event_emitter

    def _raising_emit(_name: str, _payload: dict) -> None:
        raise RuntimeError("emit down")

    set_event_emitter(_raising_emit)
    try:
        request_id = "t8-emit-fail"
        _admit_claude_sonnet_5(request_id)
        tap = _stream_tap_from_lines(
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4}}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{'
            '"input_tokens":100,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0,"output_tokens":4,'
            '"foo_details":{}}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        )
        from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
            finalize_stream_credit,
        )

        finalize_stream_credit(request_id, tap, normal_end=True)
        st = get_policy().status()
        reserve = next(e for e in st["entries"] if e["kind"] == "reserve")
        commit = next(e for e in st["entries"] if e["kind"] == "commit")
        assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))
    finally:
        set_event_emitter(None)


@pytest.mark.asyncio
async def test_openai_shape_byte_identical_after_gate() -> None:
    payload = _load_json("non_stream_full_breakdown.json")
    adapter, counts = _make_adapter(payload)
    request_body = {
        "model": "anthropic/claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    gated = await adapter.forward_chat(request_body)
    direct = adapter._anthropic_to_openai_response(payload, request_body["model"])[0]
    assert json.dumps(gated, sort_keys=True) == json.dumps(direct, sort_keys=True)
    assert counts["messages"] == 1
