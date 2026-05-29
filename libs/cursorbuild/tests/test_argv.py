"""Tests for cursorbuild.argv — flag matrices, prompt positioning, env.

The spec factory is inlined (no conftest / test_support) because Phase 1 has
no admission gate to thread a validated cwd through; a plain temp path is
enough to exercise the pure argv/env builders.
"""

from __future__ import annotations

import os

import pytest

from cursorbuild.argv import (
    _PYTHONPATH,
    _REPO_ROOT,
    _VENV_BIN,
    _VENV_ROOT,
    _build_env,
    build_argv,
)
from cursorbuild.constants import FORBIDDEN_ARGV_TOKENS, HEADLESS_MCP_FLAGS
from cursorbuild.runner_types import RunnerSpec

BIN = "/usr/bin/cursor-agent"
CWD = "/tmp/ws"
PROMPT = "do the thing"


def _spec(**overrides: object) -> RunnerSpec:
    """Build a RunnerSpec with sensible defaults, overridable per test."""
    base: dict[str, object] = {
        "dispatch_id": "d-test",
        "cwd": CWD,
        "prompt": PROMPT,
        "mode": "read_only",
        "cursor_agent_bin": BIN,
        "model": None,
        "system_context": None,
        "session_id": None,
        "timeout_seconds": None,
        "tier": "default",
    }
    base.update(overrides)
    return RunnerSpec(**base)  # type: ignore[arg-type]


# --- read-only mode ---------------------------------------------------------


def test_read_only_emits_mode_plan_and_no_mutating_flags() -> None:
    argv = build_argv(_spec(mode="read_only"))
    assert "--mode" in argv
    assert argv[argv.index("--mode") + 1] == "plan"
    assert "--approve-mcps" not in argv
    assert "--force" not in argv
    assert "--trust" not in argv


@pytest.mark.parametrize("ro_mode", ["plan", "ask"])
def test_read_only_mode_value_passthrough(ro_mode: str) -> None:
    argv = build_argv(_spec(mode="read_only", read_only_mode=ro_mode))
    assert argv[argv.index("--mode") + 1] == ro_mode


def test_plan_wins_over_force_in_read_only() -> None:
    """A read-only spec with force=True must NOT escalate to a mutating run."""
    argv = build_argv(_spec(mode="read_only", force=True))
    assert "--mode" in argv
    assert "--force" not in argv


# --- edit mode --------------------------------------------------------------


def test_edit_with_mcp_emits_permission_triple() -> None:
    argv = build_argv(_spec(mode="edit", mcp_enabled=True))
    for flag in HEADLESS_MCP_FLAGS:
        assert flag in argv
    assert "--mode" not in argv


def test_edit_without_mcp_emits_force_trust_only() -> None:
    argv = build_argv(_spec(mode="edit", mcp_enabled=False))
    assert "--force" in argv
    assert "--trust" in argv
    assert "--approve-mcps" not in argv


# --- determinism ------------------------------------------------------------


def test_build_argv_is_deterministic() -> None:
    overrides = {"mode": "edit", "mcp_enabled": True, "model": "m-1"}
    assert build_argv(_spec(**overrides)) == build_argv(_spec(**overrides))


# --- forbidden tokens -------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        _spec(mode="read_only"),
        _spec(mode="read_only", read_only_mode="ask"),
        _spec(mode="edit", mcp_enabled=True),
        _spec(mode="edit", mcp_enabled=False),
        _spec(mode="edit", model="claude-opus-4-8-high"),
        _spec(mode="edit", worktree_name="wt", worktree_base="main"),
        _spec(mode="edit", session_id="sess-1"),
    ],
)
def test_forbidden_tokens_never_present(spec: RunnerSpec) -> None:
    argv = build_argv(spec)
    for token in FORBIDDEN_ARGV_TOKENS:
        assert token not in argv


# --- head framing -----------------------------------------------------------


def test_head_uses_print_and_stream_json_no_cwd() -> None:
    argv = build_argv(_spec(mode="edit"))
    assert "--print" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "streaming-json" not in argv
    assert "--workspace" in argv
    assert argv[argv.index("--workspace") + 1] == CWD
    assert "--cwd" not in argv


# --- prompt positioning -----------------------------------------------------


def test_prompt_is_trailing_positional() -> None:
    argv = build_argv(_spec())
    assert argv[-1] == PROMPT


def test_system_context_folded_into_prompt_head() -> None:
    ctx = "operator rules"
    argv = build_argv(_spec(system_context=ctx))
    folded = argv[-1]
    assert folded.startswith(ctx)
    assert folded.endswith(PROMPT)
    assert "\n\n" in folded


# --- model / worktree / session flags ---------------------------------------


def test_model_uses_long_flag_not_dash_m() -> None:
    argv = build_argv(_spec(mode="edit", model="claude-opus-4-8-high"))
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8-high"
    assert "-m" not in argv


def test_worktree_and_resume_flags() -> None:
    argv = build_argv(
        _spec(
            mode="edit",
            worktree_name="wt",
            worktree_base="main",
            skip_worktree_setup=True,
            session_id="sess-1",
        )
    )
    assert argv[argv.index("-w") + 1] == "wt"
    assert argv[argv.index("--worktree-base") + 1] == "main"
    assert "--skip-worktree-setup" in argv
    assert argv[argv.index("--resume") + 1] == "sess-1"
    assert "--continue" not in argv


def test_continue_flag_when_no_session_id() -> None:
    argv = build_argv(_spec(mode="edit", continue_session=True))
    assert "--continue" in argv
    assert "--resume" not in argv


# --- environment ------------------------------------------------------------


def test_build_env_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("CORTEX_DB_PATH", raising=False)
    monkeypatch.delenv("TODOS_DB_PATH", raising=False)
    monkeypatch.delenv("CURSORBUILD_RECURSION_DEPTH", raising=False)
    monkeypatch.setenv("SECRET", "leak")
    monkeypatch.setenv("TERM", "xterm-256color")

    env = _build_env()

    assert env["TERM"] == "dumb"
    assert env["VIRTUAL_ENV"] == _VENV_ROOT
    assert env["PROJECT_ROOT"] == str(_REPO_ROOT)
    assert env["PYTHONPATH"] == _PYTHONPATH
    assert env["PATH"].split(os.pathsep)[0] == _VENV_BIN
    assert set(env) == {
        "PATH",
        "TERM",
        "VIRTUAL_ENV",
        "PROJECT_ROOT",
        "PYTHONPATH",
    }
    assert "SECRET" not in env
    # HOME is an OVERRIDE (set by the runner per-dispatch), never inherited.
    assert "HOME" not in env


def test_build_env_passes_recursion_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSORBUILD_RECURSION_DEPTH", "1")
    env = _build_env()
    assert env["CURSORBUILD_RECURSION_DEPTH"] == "1"


def test_pythonpath_uses_hyphenated_stargate_dir() -> None:
    expected = str(_REPO_ROOT / "services" / "universal-stargate")
    assert expected in _PYTHONPATH.split(os.pathsep)
    assert "universal_stargate" not in _PYTHONPATH
