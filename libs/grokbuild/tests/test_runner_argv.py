"""§5.11 runner argv/env tests (#11–#14, #16, #18–#20)."""

from __future__ import annotations

import pytest

from grokbuild.runner import (
    _READ_ONLY_PREFIX,
    _build_argv,
    _build_env,
)
from grokbuild.test_support import (
    PROMPT,
    runner_spec,
)


@pytest.mark.parametrize(
    ("mode", "session_id"),
    [
        ("read_only", None),
        ("edit", "abc"),
        ("edit", None),
    ],
)
def test_argv_always_includes_always_approve(
    admission: str,
    mode: str,
    session_id: str | None,
) -> None:
    spec = runner_spec(
        cwd=admission,
        mode=mode,
        session_id=session_id,
        permission_mode="plan" if mode == "read_only" else "acceptEdits",
    )
    argv = _build_argv(spec)
    assert argv.count("--always-approve") == 1


def test_permission_mode_is_label_only(admission: str) -> None:
    ro = _build_argv(
        runner_spec(cwd=admission, mode="read_only", permission_mode="plan")
    )
    ed = _build_argv(
        runner_spec(cwd=admission, mode="edit", permission_mode="acceptEdits")
    )
    assert "--permission-mode" in ro and ro[ro.index("--permission-mode") + 1] == "plan"
    assert (
        "--permission-mode" in ed
        and ed[ed.index("--permission-mode") + 1] == "acceptEdits"
    )


def test_read_only_prefix_in_rules_flag(admission: str) -> None:
    ro = _build_argv(runner_spec(cwd=admission, mode="read_only"))
    assert "-p" in ro and ro[ro.index("-p") + 1] == PROMPT
    rules = ro[ro.index("--rules") + 1]
    assert rules.startswith(_READ_ONLY_PREFIX)

    edit_plain = _build_argv(runner_spec(cwd=admission, mode="edit"))
    assert "--rules" not in edit_plain
    assert edit_plain[edit_plain.index("-p") + 1] == PROMPT

    ctx = "operator rules"
    edit_ctx = _build_argv(runner_spec(cwd=admission, mode="edit", system_context=ctx))
    assert edit_ctx[edit_ctx.index("--rules") + 1] == ctx
    assert _READ_ONLY_PREFIX not in edit_ctx[edit_ctx.index("--rules") + 1]


def test_no_worktree_flag_in_argv(admission: str) -> None:
    for mode in ("read_only", "edit"):
        argv = _build_argv(runner_spec(cwd=admission, mode=mode))
        assert "--worktree" not in argv


def test_env_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    # Strip the cortex-related allow-list keys so the strict equality
    # assertion below isn't polluted by the test runner's inherited env
    # (review C1). Docker-compose sets both in production.
    monkeypatch.delenv("CORTEX_DB_PATH", raising=False)
    monkeypatch.delenv("TODOS_DB_PATH", raising=False)
    monkeypatch.setenv("SECRET", "leak")
    monkeypatch.setenv("TERM", "xterm-256color")

    env = _build_env()

    assert env["TERM"] == "dumb"
    assert set(env.keys()) <= {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "CORTEX_DB_PATH",
        "TODOS_DB_PATH",
        "TERM",
    }
    assert set(env) == {"PATH", "HOME", "TERM"}
    assert "SECRET" not in env


@pytest.mark.parametrize(
    ("mode", "system_context", "expect_rules", "rules_contains_prefix"),
    [
        ("read_only", None, True, True),
        ("read_only", "X", True, True),
        ("edit", None, False, False),
        ("edit", "X", True, False),
    ],
)
def test_system_context_routes_via_rules_flag(
    admission: str,
    mode: str,
    system_context: str | None,
    expect_rules: bool,
    rules_contains_prefix: bool,
) -> None:
    spec = runner_spec(
        cwd=admission,
        mode=mode,
        system_context=system_context,
        permission_mode="plan" if mode == "read_only" else "acceptEdits",
    )
    argv = _build_argv(spec)
    assert argv[argv.index("-p") + 1] == PROMPT
    if not expect_rules:
        assert "--rules" not in argv
        return
    rules = argv[argv.index("--rules") + 1]
    if rules_contains_prefix:
        assert rules.startswith(_READ_ONLY_PREFIX)
        if system_context:
            assert rules.endswith(system_context)
            assert "\n\n" in rules
    else:
        assert rules == system_context
        assert _READ_ONLY_PREFIX not in rules


def test_continue_vs_resume_argv(admission: str) -> None:
    # V1: -r (strict resume) when resume_strict=True + session_id
    strict = _build_argv(
        runner_spec(cwd=admission, session_id="abc", resume_strict=True)
    )
    assert "-r" in strict and strict[strict.index("-r") + 1] == "abc"
    assert "-s" not in strict

    # V1: -s (idempotent resume) when session_id without resume_strict
    idempotent = _build_argv(runner_spec(cwd=admission, session_id="abc"))
    assert "-s" in idempotent and idempotent[idempotent.index("-s") + 1] == "abc"
    assert "-r" not in idempotent

    # Plain: no session flags
    plain = _build_argv(runner_spec(cwd=admission))
    assert "-r" not in plain and "-s" not in plain


def test_build_argv_check_flag() -> None:
    spec = runner_spec(cwd="/tmp", check=True)
    argv = _build_argv(spec)
    assert "--check" in argv


def test_build_argv_no_subagents_flag() -> None:
    spec = runner_spec(cwd="/tmp", no_subagents=True)
    argv = _build_argv(spec)
    assert "--no-subagents" in argv


def test_build_argv_omits_optional_when_none() -> None:
    spec = runner_spec(cwd="/tmp", reasoning_effort=None, effort=None)
    argv = _build_argv(spec)
    assert "--reasoning-effort" not in argv
    assert "--effort" not in argv


def test_build_argv_reasoning_effort_emitted() -> None:
    """--reasoning-effort is emitted for reasoning-capable models."""
    spec = runner_spec(cwd="/tmp", reasoning_effort="high", model="grok-3")
    argv = _build_argv(spec)
    assert "--reasoning-effort" in argv
    assert argv[argv.index("--reasoning-effort") + 1] == "high"


def test_build_argv_effort_emitted() -> None:
    """--effort is emitted for reasoning-capable models."""
    spec = runner_spec(cwd="/tmp", effort="max", model="grok-3")
    argv = _build_argv(spec)
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "max"


def test_build_argv_reasoning_effort_and_effort_both_emitted() -> None:
    """Both flags emitted simultaneously for reasoning-capable model; values are independent."""
    spec = runner_spec(
        cwd="/tmp", reasoning_effort="xhigh", effort="max", model="grok-3"
    )
    argv = _build_argv(spec)
    assert "--reasoning-effort" in argv
    assert argv[argv.index("--reasoning-effort") + 1] == "xhigh"
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "max"


def test_build_argv_suppresses_reasoning_flags_for_none_model() -> None:
    """model=None (CLI default → grok-build) suppresses --reasoning-effort and --effort.

    The grok-build model rejects both flags at the CLI level; suppression is the
    only safe path when no explicit model is chosen.
    """
    spec = runner_spec(cwd="/tmp", reasoning_effort="high", effort="max")  # model=None
    argv = _build_argv(spec)
    assert "--reasoning-effort" not in argv
    assert "--effort" not in argv


def test_build_argv_suppresses_reasoning_flags_for_non_reasoning_model() -> None:
    """grok-build registry entry (supports_reasoning_effort=False, supports_effort=False)
    suppresses both flags even when explicitly set on the spec."""
    spec = runner_spec(
        cwd="/tmp", reasoning_effort="high", effort="max", model="grok-build"
    )
    argv = _build_argv(spec)
    assert "--reasoning-effort" not in argv
    assert "--effort" not in argv


@pytest.mark.parametrize(
    ("model", "expect_effort", "expect_reasoning_effort"),
    [
        ("xai/grok-4.20-0309-reasoning", True, False),
        ("xai/grok-4.20-0309-non-reasoning", True, False),
        ("xai/grok-4.20-multi-agent-0309", True, True),
        ("xai/grok-4.3", True, True),
    ],
)
def test_build_argv_registry_splits_effort_from_reasoning_effort(
    model: str,
    expect_effort: bool,
    expect_reasoning_effort: bool,
) -> None:
    """Registry per-flag split: --effort is universal; --reasoning-effort gated by model.

    grok-4.20-* models support --effort (agent-loop tier) but suppress
    --reasoning-effort (silently swallowed by xAI CLI, API rejects it).
    grok-4.3 supports both. Unknown models pass both through.
    """
    spec = runner_spec(cwd="/tmp", effort="max", reasoning_effort="high", model=model)
    argv = _build_argv(spec)
    assert ("--effort" in argv) == expect_effort
    assert ("--reasoning-effort" in argv) == expect_reasoning_effort
