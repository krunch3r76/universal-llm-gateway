"""read_reply polling — text stable across two complete polls."""

import pytest

from web_tab.commit_verify import poll_read_reply


class _SeqPort:
    def __init__(self, seq: list[dict]) -> None:
        self._seq = list(seq)
        self._i = 0

    async def evaluate(
        self, *, ws_url: str, expression: str, await_promise: bool = False
    ):  # noqa: ARG002
        if self._i >= len(self._seq):
            return self._seq[-1]
        val = self._seq[self._i]
        self._i += 1
        return val


@pytest.mark.asyncio
async def test_read_reply_requires_stable_text_twice() -> None:
    port = _SeqPort(
        [
            {"complete": True, "text": "partial", "streaming": False},
            {"complete": True, "text": "final answer", "streaming": False},
            {"complete": True, "text": "final answer", "streaming": False},
        ]
    )
    out = await poll_read_reply(
        port,
        ws_url="ws://fake",
        site="grok",
        baseline_assistant_count=0,
        baseline_assistant_text="",
        timeout_s=2.0,
        poll_s=0.01,
    )
    assert out["ok"] is True
    assert out["text"] == "final answer"
