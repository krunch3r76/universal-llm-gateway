"""Hermetic tests for scripts/dev/cursor_sdk_pin_bump_check.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "dev" / "cursor_sdk_pin_bump_check.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("cursor_sdk_pin_bump_check", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def test_parse_pin_line_reads_requirements(mod, monkeypatch) -> None:
    req = mod.repo_root() / "requirements.host.txt"
    assert mod.parse_pin_line(req) == "1.0.30"


def test_classify_holds_on_all_pass(mod) -> None:
    checks = [
        mod.CheckResult("pin_line", "PASS", ""),
        mod.CheckResult("installed_version", "PASS", ""),
    ]
    verdict, code, bump = mod.classify(checks, "repo", "1.0.30", "1.0.30")
    assert verdict == "holds"
    assert code == 0
    assert bump is False


def test_classify_indeterminate_on_missing_pin(mod) -> None:
    checks = [mod.CheckResult("pin_line", "FAIL", "missing")]
    verdict, code, _ = mod.classify(checks, "repo", "", None)
    assert verdict == "indeterminate"
    assert code == 2


def test_classify_broken_on_fail(mod) -> None:
    checks = [
        mod.CheckResult("pin_line", "PASS", ""),
        mod.CheckResult("argv_forwarded_verbatim", "FAIL", "broken"),
    ]
    verdict, code, _ = mod.classify(checks, "repo", "1.0.30", "1.0.30")
    assert verdict == "broken"
    assert code == 1


def test_bump_eligible_canary_newer(mod) -> None:
    checks = [mod.CheckResult("pin_line", "PASS", "")]
    verdict, code, bump = mod.classify(checks, "canary", "1.0.30", "1.0.31")
    assert verdict == "holds"
    assert code == 0
    assert bump is True


def test_broken_argv_forwarding_exits_1(mod) -> None:
    bridge_bin, fail = mod._resolve_bridge_bin()
    assert fail is None and bridge_bin

    captured: dict[str, Any] = {}

    class StopAtPopenError(Exception):  # noqa: N818
        pass

    def _broken_popen(argv, **kwargs):
        bad = list(argv)
        if bridge_bin in bad:
            idx = bad.index(bridge_bin)
            bad[idx:idx] = ["--workspace", "/tmp/ws"]
        captured["argv"] = bad
        captured["env"] = kwargs.get("env")
        raise StopAtPopenError()

    result = mod.probe_argv_forwarding(bridge_bin, popen_factory=_broken_popen)
    assert result.status == "FAIL"

    checks = [
        mod.CheckResult("pin_line", "PASS", ""),
        mod.CheckResult("installed_version", "PASS", ""),
        mod.CheckResult("pin_drift", "PASS", ""),
        mod.CheckResult("sdk_launch_surface", "PASS", ""),
        mod.CheckResult("env_builder_pristine", "PASS", ""),
        mod.CheckResult("bridge_bin_resolves", "PASS", bridge_bin),
        result,
        mod.CheckResult("local_options_dirs", "PASS", ""),
        mod.CheckResult("guard_pytest", "SKIP", ""),
    ]
    verdict, code, _ = mod.classify(checks, "repo", "1.0.30", "1.0.30")
    assert verdict == "broken"
    assert code == 1


def test_canary_child_failure_indeterminate(mod, monkeypatch) -> None:
    def _bad_probe(_python: str) -> dict[str, Any]:
        return {
            "mode": "canary",
            "pinned": "",
            "installed": "",
            "python": "/nonexistent/python",
            "checks": [],
            "verdict": "indeterminate",
            "bump_eligible": False,
            "exit_code": 2,
        }

    monkeypatch.setattr(mod, "probe_via_interpreter", _bad_probe)
    assert mod.main(["--python", "/nonexistent/python"]) == 2


def test_guard_runner_monkeypatched(mod, monkeypatch) -> None:
    def _fake_guard() -> mod.CheckResult:
        return mod.CheckResult("guard_pytest", "PASS", "6 passed in 0.01s")

    monkeypatch.setattr(mod, "run_guard_pytest", _fake_guard)
    report = mod.build_report("repo", sys.executable)
    guard = next(c for c in report["checks"] if c["id"] == "guard_pytest")
    assert guard["status"] == "PASS"
    assert "passed" in guard["detail"]


def test_build_report_json_keys(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod, "run_guard_pytest", lambda: mod.CheckResult("guard_pytest", "PASS", "6 passed"))
    report = mod.build_report("repo", sys.executable)
    expected = {
        "bump_eligible",
        "checks",
        "exit_code",
        "installed",
        "mode",
        "pinned",
        "python",
        "verdict",
    }
    assert set(report) == expected


def test_canary_mode_skips_guard(mod) -> None:
    checks, _ = mod.run_inline_checks("canary", "1.0.30")
    guard = next(c for c in checks if c.id == "guard_pytest")
    assert guard.status == "SKIP"
