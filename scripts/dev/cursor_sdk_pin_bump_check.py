#!/usr/bin/env python3
"""Pin-bump discipline for the cursor-sdk argv-shim launch contract.

Purpose: verify the installed cursor_sdk matches requirements.host.txt and still
satisfies the GIW launch contract. Read-only; no network; never edits the pin.

Usage:
  python scripts/dev/cursor_sdk_pin_bump_check.py
  python scripts/dev/cursor_sdk_pin_bump_check.py --python P
  python scripts/dev/cursor_sdk_pin_bump_check.py --emit-json

Canary: python -m venv /tmp/sdk-canary && /tmp/sdk-canary/bin/pip install 'cursor-sdk==X.Y.Z'
Exit: 0 holds (WARN ok), 1 broken, 2 indeterminate.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CheckStatus = Literal["PASS", "FAIL", "WARN", "SKIP"]
Verdict = Literal["holds", "broken", "indeterminate"]
_PIN_RE = re.compile(r"^cursor-sdk==(\S+)", re.MULTILINE)
_GUARD = "services/git_integration_worker/tests/test_cursor_sdk_bridge_launch.py"
_FOOTER = (
    "note: entitlement probe not run — "
    "scripts/probes/cursor_sdk_usage_entitlement.py answers a different question"
)


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: CheckStatus
    detail: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_version(raw: str) -> tuple[int, ...]:
    out: list[int] = []
    for seg in raw.split("."):
        num = "".join(c for c in seg if c.isdigit())
        if not num:
            break
        out.append(int(num))
    return tuple(out)


def parse_pin(path: Path | None = None) -> str | None:
    path = path or repo_root() / "requirements.host.txt"
    if not path.is_file():
        return None
    m = _PIN_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _fail(cid: str, detail: str) -> CheckResult:
    return CheckResult(cid, "FAIL", detail)


def _resolve_bridge_bin() -> tuple[str | None, CheckResult | None]:
    raw = os.environ.get("CURSOR_SDK_BRIDGE_BIN", "").strip()
    if raw:
        path = os.path.abspath(raw)
        if not os.path.isfile(path) or "=" in path or path.startswith("-"):
            return None, _fail("bridge_bin_resolves", f"bad override: {raw}")
        return path, None
    try:
        from cursor_sdk._vendor import resolve_bridge_path
    except ImportError as exc:
        return None, _fail("bridge_bin_resolves", str(exc))
    path = os.path.abspath(resolve_bridge_path())
    if not os.path.isfile(path) or "=" in path or path.startswith("-"):
        return None, _fail("bridge_bin_resolves", f"bad path: {path}")
    return path, None


def _eval_argv(captured: dict[str, Any], command: list[str]) -> CheckResult:
    cid = "argv_forwarded_verbatim"
    if "argv" not in captured:
        return _fail(cid, "Popen never reached")
    argv = captured["argv"]
    if argv[: len(command)] != command:
        return _fail(cid, "command= not forwarded verbatim")
    if "--workspace" not in argv[len(command) - 1 + 1 :]:
        return _fail(cid, "--workspace missing after bin")
    env = captured.get("env")
    if env is None or env.get("HOME") != os.environ.get("HOME"):
        return _fail(cid, "overlay leaked into Popen env")
    return CheckResult(cid, "PASS", "argv shim forwarded")


def probe_argv_forwarding(bridge_bin: str, popen_factory: Callable[..., Any] | None = None) -> CheckResult:
    try:
        from cursor_sdk import Client
        from cursor_sdk import _bridge as sb
    except ImportError as exc:
        return _fail("argv_forwarded_verbatim", str(exc))
    captured: dict[str, Any] = {}

    class StopAtPopenError(Exception):  # noqa: N818
        pass

    def _capture(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs.get("env")
        raise StopAtPopenError()

    hook = popen_factory or _capture
    prior, command = sb.subprocess.Popen, ["/usr/bin/env", "HOME=/tmp/x", bridge_bin]
    sb.subprocess.Popen = hook
    try:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            command = ["/usr/bin/env", f"HOME={home}", bridge_bin]
            try:
                Client.launch_bridge(
                    command=command,
                    workspace=td,
                    state_root=str(Path(td) / "state"),
                    timeout=5.0,
                    local=None,
                )
            except BaseException:
                pass
    finally:
        sb.subprocess.Popen = prior
    return _eval_argv(captured, command)


def _sdk_checks(mode: str, pinned: str) -> tuple[list[CheckResult], str | None]:
    checks: list[CheckResult] = []
    pin = parse_pin()
    if pin is None:
        checks.append(_fail("pin_line", "cursor-sdk pin line not found"))
        return checks, None
    checks.append(CheckResult("pin_line", "PASS", f"pinned at {pin}"))
    try:
        from importlib.metadata import version as v
    except ImportError:
        checks.append(_fail("installed_version", "importlib.metadata unavailable"))
        return checks, None
    try:
        installed = v("cursor-sdk")
    except Exception as exc:
        checks.append(_fail("installed_version", f"not installed: {exc}"))
        return checks, None
    checks.append(CheckResult("installed_version", "PASS", installed))
    if installed == pin:
        checks.append(CheckResult("pin_drift", "PASS", "installed matches pin"))
    elif mode == "canary":
        checks.append(CheckResult("pin_drift", "PASS", f"canary {installed} vs pin {pin}"))
    else:
        checks.append(_fail("pin_drift", f"installed {installed} != pinned {pin}"))
    try:
        from cursor_sdk import _async_bridge, _bridge
    except ImportError as exc:
        checks.append(_fail("sdk_launch_surface", str(exc)))
        return checks, installed
    fn = _bridge._bridge_subprocess_env
    if not hasattr(_bridge.Bridge, "launch") or fn.__name__ != "_bridge_subprocess_env":
        checks.append(_fail("sdk_launch_surface", "launch surface changed"))
    elif _async_bridge._bridge_subprocess_env is not fn:
        checks.append(_fail("sdk_launch_surface", "async env fn aliased"))
    else:
        checks.append(CheckResult("sdk_launch_surface", "PASS", "launch surface intact"))
    built = fn()
    if built.get("HOME") != os.environ.get("HOME") or any(
        built.get(k) != v for k, v in os.environ.items()
    ):
        checks.append(_fail("env_builder_pristine", "os.environ mutated"))
    else:
        checks.append(CheckResult("env_builder_pristine", "PASS", "os.environ preserved"))
    bridge, bad = _resolve_bridge_bin()
    if bad:
        checks.append(bad)
    else:
        assert bridge
        checks.append(CheckResult("bridge_bin_resolves", "PASS", bridge))
        checks.append(probe_argv_forwarding(bridge))
        try:
            text = Path(bridge).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            checks.append(CheckResult("launcher_exec", "WARN", f"unreadable: {exc}"))
        else:
            if not text.lstrip().startswith("#!"):
                checks.append(CheckResult("launcher_exec", "PASS", "not a shell script"))
            else:
                warn = CheckResult("launcher_exec", "WARN", "absent")
                for line in text.splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and (
                        "cursor-sdk-bridge.js" in s or ("node" in s and ("$DIR" in s or ".js" in s))
                    ):
                        warn = CheckResult(
                            "launcher_exec",
                            "PASS" if s.startswith("exec ") else "WARN",
                            "exec present" if s.startswith("exec ") else "absent",
                        )
                        break
                checks.append(warn)
    try:
        from cursor_sdk.types import LocalAgentOptions
    except ImportError as exc:
        checks.append(_fail("local_options_dirs", str(exc)))
    else:
        fields = getattr(LocalAgentOptions, "__dataclass_fields__", {})
        ok = "dirs" in fields or "dirs" in inspect.signature(LocalAgentOptions.__init__).parameters
        checks.append(
            CheckResult("local_options_dirs", "PASS" if ok else "FAIL", "dirs field present" if ok else "missing")
        )
    if mode == "repo":
        checks.append(run_guard_pytest())
    else:
        checks.append(CheckResult("guard_pytest", "SKIP", "canary mode — guard needs GIW imports"))
    return checks, installed


def classify(checks: list[CheckResult], mode: str, pinned: str, installed: str | None) -> tuple[Verdict, int, bool]:
    if any(c.id in ("pin_line", "installed_version") and c.status == "FAIL" for c in checks):
        return "indeterminate", 2, False
    if any(c.status == "FAIL" for c in checks):
        return "broken", 1, False
    bump = mode == "canary" and installed is not None and parse_version(installed) > parse_version(pinned)
    return "holds", 0, bump


def _report(mode: str, python: str) -> dict[str, Any]:
    pinned = parse_pin() or ""
    checks, installed = _sdk_checks(mode, pinned)
    verdict, exit_code, bump = classify(checks, mode, pinned, installed)
    return {
        "mode": mode,
        "pinned": pinned,
        "installed": installed or "",
        "python": python,
        "checks": [{"id": c.id, "status": c.status, "detail": c.detail} for c in checks],
        "verdict": verdict,
        "bump_eligible": bump,
        "exit_code": exit_code,
    }


def _print_human(report: dict[str, Any]) -> None:
    for c in report["checks"]:
        print(f"{c['status']} {c['id']}: {c['detail']}")
    print(f"pinned: {report['pinned']}")
    if report["installed"]:
        print(f"installed: {report['installed']}")
    print(f"verdict: {report['verdict']}")
    print(_FOOTER)
    print(f"exit={report['exit_code']}")


def _probe_child(python: str) -> dict[str, Any]:
    try:
        done = subprocess.run(
            [python, __file__, "--emit-json", "--_canary"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except OSError:
        return {
            "mode": "canary",
            "pinned": "",
            "installed": "",
            "python": python,
            "checks": [],
            "verdict": "indeterminate",
            "bump_eligible": False,
            "exit_code": 2,
        }
    try:
        return json.loads(done.stdout.strip())
    except json.JSONDecodeError:
        return {"mode": "canary", "pinned": "", "installed": "", "python": python, "checks": [],
                "verdict": "indeterminate", "bump_eligible": False, "exit_code": 2}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--python")
    p.add_argument("--emit-json", action="store_true")
    p.add_argument("--_canary", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.python:
        report = _probe_child(args.python)
        if args.emit_json:
            print(json.dumps(report))
            return 0
        _print_human(report)
        return int(report["exit_code"])
    report = _report("canary" if args._canary else "repo", sys.executable)
    if args.emit_json:
        print(json.dumps(report))
    else:
        _print_human(report)
    return int(report["exit_code"])


parse_pin_line = parse_pin
build_report = _report
probe_via_interpreter = _probe_child
run_inline_checks = _sdk_checks


def _guard_env() -> dict[str, str]:
    env = os.environ.copy()
    root = str(repo_root())
    env["PYTHONPATH"] = f"{root}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else root
    return env


def run_guard_pytest() -> CheckResult:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", str(repo_root() / _GUARD), "-q", "-p", "no:cacheprovider"],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=_guard_env(),
    )
    summary = (done.stdout.strip().splitlines() or [""])[-1]
    return CheckResult(
        "guard_pytest",
        "PASS" if done.returncode == 0 else "FAIL",
        summary or f"exit {done.returncode}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
