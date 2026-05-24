"""C.2 — Synthetic failure injection harness for grokbuild dispatch.

Verifies that each of the four tracked failure modes fires the expected
RunnerResult shape and/or event signal with correct dispatch_id attribution.
The four cases mirror plan_phase:grokbuild-mcp-integration/phase-c:

1. dispatch_home_setup_failed  — OSError in setup_dispatch_home
2. dispatch_preflight_failed   — preflight_inspect returns an error string
3. spawn_failed                — asyncio.create_subprocess_exec raises OSError
4. no_dispatch_token (fallback) — MCP_GROK_BUILD_DISPATCH_TOKEN absent →
                                  runner uses inherited HOME, no pre-flight,
                                  sidecar carries dispatch_id for post-hoc audit

Cases 1-3 verify the runner-level result contract (reason_code, status, error).
Case 4 verifies the runner still completes and the dispatch_id is traceable in
the sidecar (the seat-attribution gap is detected via event JOIN query).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from grokbuild.runner import run_dispatch
from grokbuild.test_support import (
    FakeProc,
    install_capture_post_state,
    runner_spec,
    sidecar_lines,
)

_DISPATCH_TOKEN = "test-build-dispatch-token"


# ---------------------------------------------------------------------------
# Case 1 — dispatch_home_setup_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_home_setup_failed(
    admission: str,
    sidecar_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup_dispatch_home raises OSError → status=failed, reason_code=dispatch_home_setup_failed.

    This is the "bad MCP_AUTH_TOKEN" injection: the token is present but the
    config-toml write fails (disk full / permissions), which produces the same
    failure envelope as a bad-token scenario where filesystem writes fail.
    """
    sidecar_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MCP_GROK_BUILD_DISPATCH_TOKEN", _DISPATCH_TOKEN)

    def _raise_oserror(*_a: Any, **_kw: Any) -> None:
        raise OSError("injected: no space left on device")

    monkeypatch.setattr("grokbuild.runner.setup_dispatch_home", _raise_oserror)

    spec = runner_spec(cwd=admission, dispatch_id="fi-home-fail-id")
    rr = await run_dispatch(spec)

    assert rr.status == "failed"
    assert rr.reason_code == "dispatch_home_setup_failed"
    assert "injected: no space left on device" in (rr.error or "")
    # sidecar_path is None because the sidecar write happens after home setup.
    assert rr.sidecar_path is None
    # No tool calls parsed when we never reached communicate().
    assert rr.tool_call_names == ()


# ---------------------------------------------------------------------------
# Case 2 — dispatch_preflight_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_preflight_failed(
    admission: str,
    sidecar_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """preflight_inspect returns error string → status=failed, reason_code=dispatch_preflight_failed.

    Simulates the scenario where the grok subprocess's inspect output does
    not confirm the override HOME — typically caused by a bad/mismatched
    token or a broken grok install that ignores the custom config path.
    """
    sidecar_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MCP_GROK_BUILD_DISPATCH_TOKEN", _DISPATCH_TOKEN)

    dispatch_home = sidecar_root / "fi-preflight-id-home"
    dispatch_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "grokbuild.runner.setup_dispatch_home",
        lambda *_a, **_kw: dispatch_home,
    )
    monkeypatch.setattr(
        "grokbuild.runner.preflight_inspect",
        lambda *_a, **_kw: "injected: config userPath is not under dispatch home",
    )

    spec = runner_spec(cwd=admission, dispatch_id="fi-preflight-id")
    rr = await run_dispatch(spec)

    assert rr.status == "failed"
    assert rr.reason_code == "dispatch_preflight_failed"
    assert "injected: config userPath is not under dispatch home" in (rr.error or "")
    assert rr.sidecar_path is None
    assert rr.tool_call_names == ()


# ---------------------------------------------------------------------------
# Case 3 — spawn_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_failed(
    admission: str,
    sidecar_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncio.create_subprocess_exec raises OSError → status=failed, reason_code=spawn_failed.

    The sidecar must have a 'started' record (written before spawn) and an
    'exit' record with reason_code=spawn_failed so the dispatch_id is
    traceable in sidecar even when the subprocess never ran.
    """
    sidecar_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("MCP_GROK_BUILD_DISPATCH_TOKEN", raising=False)

    async def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("injected: [Errno 2] No such file or directory: 'grok'")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

    spec = runner_spec(cwd=admission, dispatch_id="fi-spawn-fail-id")
    rr = await run_dispatch(spec)

    assert rr.status == "failed"
    assert rr.reason_code == "spawn_failed"
    assert "injected" in (rr.error or "")
    assert rr.tool_call_names == ()

    # Sidecar path exists (written before spawn) and carries started+exit.
    assert rr.sidecar_path is not None
    assert "fi-spawn-fail-id" in rr.sidecar_path
    lines = sidecar_lines(sidecar_root, "fi-spawn-fail-id")
    assert any(r.get("phase") == "started" for r in lines)
    exit_rec = next((r for r in lines if r.get("phase") == "exit"), None)
    assert exit_rec is not None
    assert exit_rec.get("reason_code") == "spawn_failed"


# ---------------------------------------------------------------------------
# Case 4 — no dispatch token (attribution fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_dispatch_token_fallback_completes(
    admission: str,
    sidecar_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No MCP_GROK_BUILD_DISPATCH_TOKEN → runner falls back to inherited HOME.

    In this mode no dispatch-scoped config.toml is generated and no pre-flight
    runs.  The grok subprocess will use the real ~/.grok/config.toml, so inner
    MCP traffic is attributed to seat=grok-direct (not grok-build-dispatch).
    This is the "unset header env" case in Phase C — the seat-attribution gap
    is detectable post-hoc via a JOIN query between mcp.request.* events and
    mcp.grokbuild.dispatch.toolcalls.

    Assertions:
    - No pre-flight failure (status == completed with our fake proc).
    - dispatch_id embedded in sidecar path for post-hoc attribution audit.
    - tool_call_names parses correctly from the fake streaming-JSON stdout.
    """
    sidecar_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("MCP_GROK_BUILD_DISPATCH_TOKEN", raising=False)
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")

    # Fake stdout with one tool_use record so parse_tool_calls can be exercised.
    fake_stdout = b'{"type":"text","data":"hi"}\n{"type":"tool_use","name":"cortex"}\n'
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProc(stdout=fake_stdout)),
    )

    spec = runner_spec(cwd=admission, dispatch_id="fi-no-token-id")
    rr = await run_dispatch(spec)

    assert rr.status == "completed"
    assert rr.reason_code == ""
    # C.1(ii): tool call parsed from fake stdout.
    assert rr.tool_call_names == ("cortex",)
    # Sidecar path encodes dispatch_id for post-hoc JOIN.
    assert rr.sidecar_path is not None
    assert "fi-no-token-id" in rr.sidecar_path
    lines = sidecar_lines(sidecar_root, "fi-no-token-id")
    assert any(r.get("phase") == "started" for r in lines)
