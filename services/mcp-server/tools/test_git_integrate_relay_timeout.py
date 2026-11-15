"""Floor for the git_land and git_integrate MCP relay timeouts.

2100.0 is ops_common._GATE_TIMEOUT (300.0) plus the 1800.0 suite budget cited in
tools/git_integrate.py; land integration uses ops_common._suite_digest_timeout()
(land_gate_pytest_session_timeout_s() + 120, default 1020.0). This test does not
import libs.git_integrate.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from tools.git_integrate import register_git_integrate_tools

# _GATE_TIMEOUT 300.0 + land suite budget via _suite_digest_timeout() (ops_common.py).
_RELAY_TIMEOUT_FLOOR = 2100.0


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def test_git_land_and_integrate_relay_timeout_covers_suite_gate() -> None:
    recorder = _Recorder()
    register_git_integrate_tools(recorder)  # type: ignore[arg-type]
    captured: list[float] = []

    async def _fake_relay(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.append(float(kwargs["timeout"]))
        return {}

    async def _call_both() -> None:
        with patch("tools.git_integrate._relay", new=_fake_relay):
            await recorder.functions["git_integrate"](
                "arc", "phase", "/tmp/wt", "approval", "abc"
            )
            await recorder.functions["git_land"](
                "arc", "phase", "/tmp/wt", "approval", "abc"
            )

    asyncio.run(_call_both())
    assert len(captured) == 2
    assert all(timeout >= _RELAY_TIMEOUT_FLOOR for timeout in captured)
