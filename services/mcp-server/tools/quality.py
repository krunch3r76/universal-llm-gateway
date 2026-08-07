"""Quality gate tools — ruff lint, compileall, and import resolution.

Allows agents to verify code quality before committing changes.
Runs against files in the project directory mounted at /data/project.

compileall validates syntax only; import checking executes importlib so
broken relative imports (e.g. package-shadow splits) fail before restart.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._project_paths import repo_base_for, resolve_existing_files
from ._skill_catalog_gate import run_skill_catalog_gate

if TYPE_CHECKING:
    from fastmcp import FastMCP

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))
_TIMEOUT = 30
_TEST_TIMEOUT = 120
# Each suite: source_segments trigger it when an edited path contains any segment;
# test_paths run (repo-root-relative); marker is the -m expression or None to run
# by explicit path. Add new on-seat suites here — one table, no new MCP tool.
_OFFLINE_TEST_SUITES: tuple[dict[str, Any], ...] = (
    {
        "name": "skill_suggest_slug_uri",
        "source_segments": (
            "/libs/cortex_store/routes/_skill_suggest",
            "/libs/cortex_store/test_skill_suggest_slug_uri",
        ),
        "test_paths": ("libs/cortex_store/test_skill_suggest_slug_uri.py",),
        "marker": "offline",
        "extra_pythonpath": ("libs",),
    },
    {
        "name": "lane_a_registry",
        "source_segments": ("/libs/llm_adapters/", "/libs/model_id/"),
        "test_paths": ("libs/llm_adapters", "libs/model_id"),
        "marker": "offline",
        "extra_pythonpath": ("libs",),
    },
    {
        "name": "implement_admission",
        "source_segments": ("/libs/implement_admission/", "/systems/frontier_consult/"),
        "test_paths": (
            "services/universal-stargate/systems/frontier_consult/test_implement_admission_materialize.py",
            "services/universal-stargate/systems/frontier_consult/test_implement_admission_normalize.py",
            "services/universal-stargate/systems/frontier_consult/test_implement_admission_routing.py",
            "services/universal-stargate/systems/frontier_consult/test_implement_admission_shadow_replay.py",
            "services/universal-stargate/systems/frontier_consult/test_team_handoff.py",
            "services/universal-stargate/systems/frontier_consult/test_admission_generate.py",
        ),
        "marker": None,
        "extra_pythonpath": ("libs", "services/universal-stargate"),
        "import_mode": "importlib",
        "workspaces_root_from_repo_parent": True,
    },
    {
        "name": "skill_catalog_parity",
        "source_segments": (
            "/libs/claude_bundles/catalog.py",
            "/config/skills.yaml",
            "/cursor-plugins/ulg-ecosystem/SKILLS_CENSUS.txt",
            "/scripts/cortex/validate_skill_catalog.py",
            "/scripts/hooks/validate_skill_catalog_staged.py",
            "/.cursor/skills/",
        ),
        "test_paths": ("libs/claude_bundles/test_catalog.py",),
        "marker": None,
        "extra_pythonpath": ("libs",),
    },
)


def register_quality_tools(mcp: FastMCP) -> None:
    """Register code quality gate tools."""

    @mcp.tool(title="Quality Gate")
    def quality_gate(files: list[str]) -> dict[str, Any]:
        """Run ruff, compileall, import checks, and Lane A offline pytest when applicable."""
        t0 = monotonic_now()
        record("mcp.quality.gate.called", file_count=len(files))

        existing = _resolve_existing_files(files)
        if not existing:
            return {"passed": False, "error": "No valid files found"}

        ruff_result = _run_ruff(existing)
        compile_result = _run_compileall(existing)
        import_result = _run_import_check(existing)
        tests_result = _run_offline_tests(existing)
        catalog_result = _run_event_catalog_gate(existing)
        skill_catalog_result = run_skill_catalog_gate(
            existing, repo_root=repo_base_for(_PROJECT_ROOT)
        )

        passed = (
            ruff_result["passed"]
            and compile_result["passed"]
            and import_result["passed"]
            and tests_result["passed"]
            and catalog_result["passed"]
            and skill_catalog_result["passed"]
        )
        duration = monotonic_now() - t0

        record(
            "mcp.quality.gate.completed",
            passed=passed,
            duration_s=round(duration, 3),
        )

        return {
            "passed": passed,
            "ruff": ruff_result,
            "compile": compile_result,
            "imports": import_result,
            "tests": tests_result,
            "event_catalog": catalog_result,
            "skill_catalog": skill_catalog_result,
        }


def _resolve_existing_files(files: list[str]) -> list[str]:
    return resolve_existing_files(files, root=_PROJECT_ROOT.resolve())


def _paths_touch_event_catalog(files: list[str]) -> bool:
    for path in files:
        if path.startswith("docs/event-contracts"):
            return True
        if not path.endswith(".py"):
            continue
        if not any(
            path.startswith(f"{root}/") for root in ("services", "libs", "systems")
        ):
            continue
        parts = path.split("/")
        if "events" in parts or parts[-1].startswith("events"):
            return True
    return False


def _run_event_catalog_gate(files: list[str]) -> dict[str, bool | str]:
    if not _paths_touch_event_catalog(files):
        return {
            "passed": True,
            "skipped": True,
            "output": "no event-source files; catalog sync skipped",
        }

    repo_base = repo_base_for(_PROJECT_ROOT)
    sync_cmd = [sys.executable, "-m", "scripts.gen_event_catalog", "sync"]
    check_cmd = [sys.executable, "-m", "scripts.gen_event_catalog", "check"]
    try:
        sync = subprocess.run(
            sync_cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(repo_base),
        )
        check = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(repo_base),
        )
        output = (sync.stderr + sync.stdout + check.stderr + check.stdout).strip()
        return {
            "passed": check.returncode == 0,
            "synced": sync.returncode == 0,
            "output": output[:4000] if output else "(no output)",
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "event catalog sync/check timed out"}


def _run_ruff(files: list[str]) -> dict[str, bool | str]:
    """Run ruff check on files under the owning repo root.

    ``cwd`` is pinned to ``repo_base`` (same as event-catalog / import-check
    siblings) so first-party isort settings resolve from the project
    ``pyproject.toml`` rather than the MCP process cwd. Measuring the same
    absolute path from outside a project root can emit phantom I001.
    """
    repo_base = repo_base_for(_PROJECT_ROOT)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", *files],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(repo_base),
        )
        return {
            "passed": result.returncode == 0,
            "output": result.stdout[:2000] if result.stdout else result.stderr[:2000],
        }
    except FileNotFoundError:
        return {"passed": False, "output": "python executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "ruff timed out"}


def _run_compileall(files: list[str]) -> dict[str, bool | str]:
    """Run compileall on files."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", *files],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        return {
            "passed": result.returncode == 0,
            "output": result.stdout[:2000] if result.stdout else result.stderr[:2000],
        }
    except FileNotFoundError:
        return {"passed": False, "output": "python executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "compileall timed out"}


# Verbatim SystemExit strings from scripts/check-imports for the cases where it
# cannot resolve a file's import context (out-of-scope / nothing to check). These
# are structural rejections, NOT import failures. Keep in sync with the script.
_IMPORT_CHECK_SKIP_MARKERS = (
    "path outside stargate/libs trees",
    "no Python files to check",
    "not a .py file or directory",
)
# A real failure prints one of these.
_IMPORT_CHECK_FAILURE_MARKERS = ("FAILED", "Traceback (most recent call last)")


def _classify_import_check(returncode: int, output: str) -> str:
    """Classify a check-imports run as 'passed' | 'skipped' | 'failed'.

    passed  — returncode 0 (all targets imported cleanly).
    skipped — returncode != 0 but output is an out-of-scope / nothing-to-check
              structural rejection AND carries no genuine-failure marker.
    failed  — a real import resolution failure (failure marker wins on tie).
    """
    if returncode == 0:
        return "passed"
    has_failure = any(m in output for m in _IMPORT_CHECK_FAILURE_MARKERS)
    if has_failure:
        return "failed"
    if any(m in output for m in _IMPORT_CHECK_SKIP_MARKERS):
        return "skipped"
    return "failed"


def _run_import_check(files: list[str]) -> dict[str, bool | str]:
    """Execute scripts/check-imports on Python files (runtime import resolution)."""
    py_files = [path for path in files if path.endswith(".py")]
    if not py_files:
        return {"passed": True, "output": "no Python files to import-check"}

    repo_base = repo_base_for(_PROJECT_ROOT)
    check_script = repo_base / "scripts" / "check-imports"
    if not check_script.exists():
        # Infra absence (script not baked into this image) must NOT sink the gate;
        # mirror the no-py-files branch. Reserve passed:False for a check that ran
        # and found a real import failure.
        return {
            "passed": True,
            "skipped": True,
            "output": f"check-imports unavailable (skipped): {check_script}",
        }

    stargate_root = (repo_base / "services" / "universal-stargate").resolve()
    needs_stargate_entry = any(
        Path(path).resolve().is_relative_to(stargate_root) for path in py_files
    )

    cmd = [sys.executable, str(check_script)]
    if needs_stargate_entry:
        cmd.append("--stargate-entry")
    cmd.extend(py_files)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(_TIMEOUT, 60),
            cwd=str(repo_base),
        )
        output = (result.stdout + result.stderr).strip()
        verdict = _classify_import_check(result.returncode, output)
        check_result: dict[str, bool | str] = {
            "passed": verdict != "failed",
            "output": output[:4000] if output else "(no output)",
        }
        if verdict == "skipped":
            check_result["skipped"] = True
        if "ENV_GAP" in output:
            # check-imports reported a missing third-party dep in the gate's
            # execution environment (entry sweep). Reported, not gate-failing.
            check_result["env_gap"] = True
        return check_result
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "check-imports timed out"}


def _matched_offline_suites(files: list[str]) -> list[dict[str, Any]]:
    return [
        suite
        for suite in _OFFLINE_TEST_SUITES
        if any(seg in f for seg in suite["source_segments"] for f in files)
    ]


def _run_one_offline_suite(
    suite: dict[str, Any], repo_root: Path
) -> dict[str, bool | str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        str(repo_root / p) for p in suite["extra_pythonpath"]
    )
    if suite.get("workspaces_root_from_repo_parent"):
        env["WORKSPACES_ROOT"] = str(repo_root.parent)
    cmd = [sys.executable, "-m", "pytest"]
    if suite.get("import_mode"):
        cmd += ["--import-mode", suite["import_mode"]]
    if suite["marker"]:
        cmd += ["-m", suite["marker"]]
    cmd += ["-q", "--no-header", "-p", "no:cacheprovider", *suite["test_paths"]]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT,
            cwd=str(repo_root),
            env=env,
        )
    except FileNotFoundError:
        return {"passed": False, "output": "python executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": f"{suite['name']} tests timed out"}
    out = (result.stdout + result.stderr).strip()
    return {
        "passed": result.returncode == 0,
        "output": out[:4000] if out else "(no output)",
    }


def _run_offline_tests(
    files: list[str], *, run_tests: bool = True
) -> dict[str, bool | str]:
    matched = _matched_offline_suites(files) if run_tests else []
    if not matched:
        return {"passed": True, "output": "no offline-closure files touched; skipped"}

    # Shared probe — pytest-absence is intentionally FAIL-CLOSED (see test
    # test_run_offline_tests_fail_closed_when_pytest_absent). Do not degrade.
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {
            "passed": False,
            "output": "pytest probe failed; offline tests unavailable",
        }
    if probe.returncode != 0:
        return {
            "passed": False,
            "output": "pytest unavailable in image; offline tests blocked",
        }

    repo_root = repo_base_for(_PROJECT_ROOT)
    results = [(s["name"], _run_one_offline_suite(s, repo_root)) for s in matched]
    all_passed = all(r["passed"] for _, r in results)
    if len(results) == 1:
        # Single-suite path stays verbatim (preserves the pinned libs assertion).
        return {"passed": all_passed, "output": str(results[0][1]["output"])}
    joined = "\n".join(f"[{name}] {r['output']}" for name, r in results)
    return {"passed": all_passed, "output": joined[:4000]}
