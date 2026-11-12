"""Worktree-first pytest runner for land gates and suite-digest measurement.

Land integration runs inside arc/dispatch worktrees; imports must resolve from
that tree, not a shadowed hub checkout on PYTHONPATH. Per-test and session
timeouts fail hung tests before the outer gate wrapper sits for hours.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_DEFAULT_PER_TEST_TIMEOUT_S = 120
_DEFAULT_SESSION_TIMEOUT_S = 900
_PROBE_MODULES = (
    "services.git_integration_worker.config",
    "services.git_integration_worker.giw_suite_digest_gate",
)

_MODULE = "services.git_integration_worker.giw_land_pytest"


def land_gate_pytest_per_test_timeout_s() -> int:
    raw = os.environ.get("ULG_LAND_GATE_PYTEST_PER_TEST_TIMEOUT_S", "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _DEFAULT_PER_TEST_TIMEOUT_S


def land_gate_pytest_session_timeout_s() -> int:
    raw = os.environ.get("ULG_LAND_GATE_PYTEST_SESSION_TIMEOUT_S", "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _DEFAULT_SESSION_TIMEOUT_S


def universal_python() -> Path:
    """Venv entrypoint; do not resolve symlinks (resolved path drops site-packages)."""
    candidate = Path.home() / ".venvs/universal/bin/python"
    return candidate if candidate.is_file() else Path(sys.executable)


def worktree_first_environ(worktree: Path) -> dict[str, str]:
    """Prepend *worktree* to PYTHONPATH so the arc tree wins over hub shadows."""
    env = os.environ.copy()
    root = str(worktree.resolve())
    sep = os.pathsep
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}{sep}{existing}" if existing else root
    env["ULG_LAND_GATE_WORKTREE"] = root
    env["ULG_LAND_GATE_PROBE_MODULES"] = ",".join(_PROBE_MODULES)
    return env


def pytest_timeout_argv() -> list[str]:
    per_test = land_gate_pytest_per_test_timeout_s()
    return [f"--timeout={per_test}", "--timeout-method=thread"]


def _proof_source() -> str:
    return (
        "import importlib, os, sys\n"
        "root = os.path.realpath(os.environ['ULG_LAND_GATE_WORKTREE'])\n"
        "for name in os.environ['ULG_LAND_GATE_PROBE_MODULES'].split(','):\n"
        "    if not name:\n"
        "        continue\n"
        "    mod = importlib.import_module(name)\n"
        "    path = os.path.realpath(getattr(mod, '__file__', '') or '')\n"
        "    if not path:\n"
        "        print(f'ULG_MODULE_ORIGIN {name} <no __file__>', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    print(f'ULG_MODULE_ORIGIN {name} {path}', file=sys.stderr)\n"
        "    if not (path == root or path.startswith(root + os.sep)):\n"
        "        print(\n"
        "            f'module {name} loaded outside worktree {root}: {path}',\n"
        "            file=sys.stderr,\n"
        "        )\n"
        "        raise SystemExit(3)\n"
    )


def run_module_origin_proof(
    worktree: Path,
    *,
    python: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Subprocess proof that probe modules load from *worktree*."""
    exe = str(python or universal_python())
    merged = env if env is not None else worktree_first_environ(worktree)
    return subprocess.run(
        [exe, "-c", _proof_source()],
        cwd=str(worktree.resolve()),
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
        env=merged,
    )


def run_land_gate_pytest(
    worktree: Path,
    pytest_argv: list[str],
    *,
    basetemp: str | None = None,
    python: Path | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run pytest under worktree-first PYTHONPATH with timeout and origin proof."""
    wt = worktree.resolve()
    exe = python or universal_python()
    env = worktree_first_environ(wt)
    proof = run_module_origin_proof(wt, python=exe, env=env)
    if proof.returncode != 0:
        return subprocess.CompletedProcess(
            args=["module_origin_proof"],
            returncode=proof.returncode or 1,
            stdout=proof.stdout or "",
            stderr=proof.stderr or "",
        )
    cmd: list[str] = [str(exe), "-m", "pytest", *pytest_timeout_argv(), *pytest_argv]
    if basetemp is not None:
        cmd.extend(["--basetemp", basetemp])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(wt),
            capture_output=True,
            text=True,
            timeout=float(land_gate_pytest_session_timeout_s()),
            check=False,
            env=env,
        )
        proof_tail = proof.stderr or ""
        if proc.stderr:
            proc = subprocess.CompletedProcess(
                args=proc.args,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proof_tail + proc.stderr,
            )
        elif proof_tail:
            proc = subprocess.CompletedProcess(
                args=proc.args,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proof_tail,
            )
        return proc
    except subprocess.TimeoutExpired as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-500:]
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=(exc.stdout or b"").decode(errors="replace"),
            stderr=f"land_gate pytest session timeout "
            f"({land_gate_pytest_session_timeout_s()}s)\n{tail}",
        )
    except OSError:
        return None


def land_gate_pytest_cli_invocation() -> str:
    """Shell fragment: ``python -m … giw_land_pytest run`` (cwd = worktree)."""
    return f"python -m {_MODULE} run"


def suite_digest_pytest_argv() -> list[str]:
    return [
        "services/git_integration_worker/tests",
        "-q",
        "--tb=no",
        "-p",
        "no:cacheprovider",
    ]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "run":
        sys.stderr.write(f"usage: python -m {_MODULE} run [pytest args...]\n")
        return 2
    base = tempfile.mkdtemp(prefix="giw-land-pytest-")
    try:
        proc = run_land_gate_pytest(
            Path.cwd(),
            args[1:],
            basetemp=base,
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if proc is None:
        return 125
    if proc.stdout:
        sys.stdout.write(proc.stdout if proc.stdout.endswith("\n") else proc.stdout + "\n")
    if proc.stderr:
        sys.stderr.write(proc.stderr if proc.stderr.endswith("\n") else proc.stderr + "\n")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
