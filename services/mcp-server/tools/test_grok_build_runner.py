"""§5.11 runner argv/env/sidecar tests (#11–#14, #16, #18–#20, #23)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._grok_build_runner import (
    _READ_ONLY_PREFIX,
    STDOUT_MAX,
    _build_argv,
    _build_env,
    run_dispatch,
)
from tools._grok_build_test_support import (
    PROMPT,
    FakeProc,
    install_capture_post_state,
    install_subprocess_exec,
    runner_spec,
    sidecar_lines,
)


@pytest.mark.parametrize(
    ("mode", "session_id", "continue_recent", "output_format"),
    [
        ("read_only", None, False, "json"),
        ("edit", "abc", False, "streaming-json"),
        ("read_only", None, True, "json"),
        ("edit", None, False, "streaming-json"),
    ],
)
def test_argv_always_includes_always_approve(
    admission: str,
    mode: str,
    session_id: str | None,
    continue_recent: bool,
    output_format: str,
) -> None:
    spec = runner_spec(
        cwd=admission,
        mode=mode,
        session_id=session_id,
        continue_recent=continue_recent,
        output_format=output_format,
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


@pytest.mark.asyncio
async def test_stdout_truncation(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar_root.mkdir(parents=True, exist_ok=True)
    big = b"x" * (STDOUT_MAX + 1000)
    install_subprocess_exec(monkeypatch, FakeProc(stdout=big))
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")

    spec = runner_spec(cwd=admission)
    rr = await run_dispatch(spec)

    assert rr.truncated is True
    assert len(rr.stdout) <= STDOUT_MAX
    lines = sidecar_lines(sidecar_root, spec.dispatch_id)
    chunk = next(r for r in lines if r.get("phase") == "stdout_chunk")
    assert len(chunk["data"]) >= STDOUT_MAX


@pytest.mark.asyncio
async def test_sidecar_records_git_audit_fields(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre = ""
    post = " M tracked.txt\n"
    diff = " tracked.txt | 1 +\n"
    install_capture_post_state(monkeypatch, status_post=post, diff_stat=diff)
    install_subprocess_exec(monkeypatch)
    sidecar_root.mkdir(parents=True, exist_ok=True)
    spec = runner_spec(
        cwd=admission, git_status_pre=pre, dispatch_id="audit-sidecar-id"
    )
    await run_dispatch(spec)

    lines = sidecar_lines(sidecar_root, "audit-sidecar-id")
    started = next(r for r in lines if r["phase"] == "started")
    exit_line = next(r for r in lines if r["phase"] == "exit")
    assert started["git_status_pre"] == pre
    assert exit_line["git_status_post"] == post
    assert exit_line["git_diff_stat"] == diff


def test_env_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("SECRET", "leak")
    monkeypatch.setenv("TERM", "xterm-256color")

    env = _build_env()

    assert env["TERM"] == "dumb"
    assert set(env.keys()) <= {"PATH", "HOME", "LANG", "LC_ALL", "TERM"}
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
    cont = _build_argv(runner_spec(cwd=admission, continue_recent=True))
    assert "--continue" in cont and "--resume" not in cont

    resume = _build_argv(runner_spec(cwd=admission, session_id="abc"))
    assert "--resume" in resume and resume[resume.index("--resume") + 1] == "abc"
    assert "--continue" not in resume

    plain = _build_argv(runner_spec(cwd=admission))
    assert "--continue" not in plain and "--resume" not in plain
