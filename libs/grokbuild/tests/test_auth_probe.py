"""Unit tests for grokbuild.auth_probe — probe behavior, env injection, cache."""

from __future__ import annotations

import subprocess

import pytest

from grokbuild.auth_probe import (
    AuthStatus,
    _CachedAuthProbe,
    _probe_env,
    probe_grok_auth,
)


def _proc(cmd: list, rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, rc, stdout="model-list\n" if rc == 0 else "", stderr=stderr)


# ── probe_grok_auth ──────────────────────────────────────────────────────────


class TestProbeGrokAuth:
    def test_exit_zero_returns_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both warmup calls exit 0 → AuthStatus.OK."""
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _proc(cmd, 0))
        result = probe_grok_auth()
        assert result.status == AuthStatus.OK

    def test_warmup_first_fail_second_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First call exit 1, second exit 0 → OK; cold-start false-negative avoided."""
        count = {"n": 0}

        def fake_run(cmd, **kw):
            count["n"] += 1
            return _proc(cmd, 1 if count["n"] == 1 else 0, stderr="cold-start")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("grokbuild.auth_probe._WARMUP_SLEEP_S", 0)

        result = probe_grok_auth()

        assert result.status == AuthStatus.OK
        assert count["n"] == 2

    def test_both_calls_fail_returns_expired_with_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both warmup calls exit 1 → EXPIRED; detail carries the stderr snippet."""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: _proc(cmd, 1, stderr="token has expired: please reauth"),
        )
        monkeypatch.setattr("grokbuild.auth_probe._WARMUP_SLEEP_S", 0)

        result = probe_grok_auth()

        assert result.status == AuthStatus.EXPIRED
        assert "expired" in result.detail

    def test_file_not_found_returns_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError → MISSING with canonical 'grok binary not found' message."""

        def fake_run(cmd, **kw):
            raise FileNotFoundError("grok: no such file")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = probe_grok_auth()

        assert result.status == AuthStatus.MISSING
        assert "grok binary not found" in result.detail

    def test_timeout_expired_returns_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """subprocess.TimeoutExpired → MISSING."""

        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 30)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = probe_grok_auth()

        assert result.status == AuthStatus.MISSING


# ── _probe_env HOME injection (§F3 regression guard) ────────────────────────


class TestProbeEnvHomInjection:
    def test_probe_env_adds_home_from_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_probe_env() re-injects HOME even when _build_env strips it."""
        monkeypatch.setenv("HOME", "/real/home/injected")
        # Simulate _build_env stripping HOME (as it does for dispatch subprocesses).
        monkeypatch.setattr("grokbuild.auth_probe._build_env", lambda: {})

        env = _probe_env()

        assert env.get("HOME") == "/real/home/injected"

    def test_probe_grok_auth_subprocess_receives_home(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env kwarg passed to subprocess.run contains HOME from os.environ."""
        monkeypatch.setenv("HOME", "/captured/home")
        monkeypatch.setattr("grokbuild.auth_probe._build_env", lambda: {})

        captured: dict = {}

        def fake_run(cmd, **kw):
            captured.update(kw.get("env") or {})
            return _proc(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        probe_grok_auth()

        assert captured.get("HOME") == "/captured/home"


# ── _CachedAuthProbe ─────────────────────────────────────────────────────────


class TestCachedAuthProbe:
    def test_ok_returns_true_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ok() returns True when probe_grok_auth returns OK."""
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _proc(cmd, 0))
        probe = _CachedAuthProbe()
        assert probe.ok() is True

    def test_ok_caches_true_short_circuits_on_second_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a True result, subsequent ok() calls short-circuit without probing."""
        count = {"n": 0}

        def fake_run(cmd, **kw):
            count["n"] += 1
            return _proc(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        probe = _CachedAuthProbe()

        assert probe.ok() is True
        calls_after_first = count["n"]  # two warmup calls

        assert probe.ok() is True  # must short-circuit
        assert count["n"] == calls_after_first  # no additional subprocess call

    def test_ok_does_not_cache_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """False is NOT cached; a subsequent call re-probes and can return True."""
        count = {"n": 0}

        def fake_run(cmd, **kw):
            count["n"] += 1
            # First two calls (warmup 1): fail; subsequent: succeed.
            return _proc(cmd, 1 if count["n"] <= 2 else 0, stderr="expired")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("grokbuild.auth_probe._WARMUP_SLEEP_S", 0)

        probe = _CachedAuthProbe()

        assert probe.ok() is False  # not cached
        assert probe.ok() is True  # re-probes on next call

    def test_reset_clears_cached_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """reset() clears cached True so the next ok() re-runs probe_grok_auth."""
        count = {"n": 0}

        def fake_run(cmd, **kw):
            count["n"] += 1
            return _proc(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        probe = _CachedAuthProbe()

        probe.ok()  # caches True
        count_after_first = count["n"]

        probe.reset()
        probe.ok()  # must re-probe

        assert count["n"] > count_after_first
