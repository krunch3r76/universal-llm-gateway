"""Hermetic tests for ``python -m scripts.model_manager.ui`` tty/argv guards."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.__main__ import main


@pytest.mark.offline
def test_non_tty_stdin_rejects_without_run(capsys: pytest.CaptureFixture[str]) -> None:
    called: list[str] = []

    code = main([], stdin_isatty=False, run_fn=lambda: called.append("run"))

    assert code == 2
    assert called == []
    err = capsys.readouterr().err
    assert "manage.sock" in err or "MCP manage" in err
    assert "TTY" in err or "tty" in err


@pytest.mark.offline
def test_unexpected_argv_rejects_without_run(capsys: pytest.CaptureFixture[str]) -> None:
    called: list[str] = []

    code = main(["status"], stdin_isatty=True, run_fn=lambda: called.append("run"))

    assert code == 2
    assert called == []
    err = capsys.readouterr().err
    assert "usage:" in err or "unexpected" in err
    assert "manage" in err.lower()


@pytest.mark.offline
def test_tty_and_no_argv_calls_run(capsys: pytest.CaptureFixture[str]) -> None:
    called: list[str] = []

    code = main(
        [],
        stdin_isatty=True,
        run_fn=lambda: called.append("run"),
        acquire_lock_fn=lambda: 99,
        release_lock_fn=lambda fd: None,
    )

    assert code == 0
    assert called == ["run"]
    assert capsys.readouterr().err == ""


@pytest.mark.offline
def test_argv_rejection_precedes_tty_check(capsys: pytest.CaptureFixture[str]) -> None:
    """Extra argv must fail even when stdin is also non-tty."""
    called: list[str] = []

    code = main(["status"], stdin_isatty=False, run_fn=lambda: called.append("run"))

    assert code == 2
    assert called == []
    err = capsys.readouterr().err
    assert "unexpected" in err or "usage:" in err
