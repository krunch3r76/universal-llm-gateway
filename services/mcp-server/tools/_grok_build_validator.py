"""Pure validation for grok_build dispatch admission."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_SIDECAR_DIR = Path("/tmp/logs/grok-build")
_VALID_OPS = frozenset({"dispatch"})
_VALID_OUTPUT_FORMATS = frozenset({"json", "streaming-json"})
_PERMISSION_BY_MODE = {"read_only": "plan", "edit": "acceptEdits"}


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    reason: str
    reason_code: str
    permission_mode: str = ""
    grok_path: str = ""
    git_status_pre: str = ""
    dirty_admission: bool = False


@functools.lru_cache(maxsize=1)
def _resolve_grok_path() -> str | None:
    return shutil.which("grok")


_grok_models_cached: bool = False


def _grok_models_ok() -> bool:
    """Verify grok auth via ``grok models``. Caches only True returns.

    A False result is NOT cached — the next call re-runs the subprocess check.
    This avoids the lru_cache cold-start poisoning pattern where a single early
    False (e.g. from the first-call transient on a fresh container process)
    would block all dispatches until MCP restarts. See
    docs/agent-guides/grok-build-dispatch.md §3.4 and §7.2.
    """
    global _grok_models_cached
    if _grok_models_cached:
        return True
    try:
        proc = subprocess.run(
            ["grok", "models"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode == 0:
        _grok_models_cached = True
        return True
    return False


def _grok_models_cache_clear() -> None:
    """Clear the cached True state.

    Compatibility shim for callers using ``_grok_models_ok.cache_clear()`` —
    mirrors the ``functools.lru_cache`` attribute name so test fixtures and
    ops procedures continue to work after the cache-only-True refactor.
    """
    global _grok_models_cached
    _grok_models_cached = False


# Python functions are objects; attach cache_clear as an attribute so existing
# callers (test fixtures via ``clear_validator_caches``) keep working.
_grok_models_ok.cache_clear = _grok_models_cache_clear  # type: ignore[attr-defined]


def _reject(reason_code: str, reason: str) -> ValidationResult:
    return ValidationResult(ok=False, reason=reason, reason_code=reason_code)


def validate_dispatch(
    op: str,
    cwd: str,
    mode: Literal["read_only", "edit"],
    session_id: str | None,
    continue_recent: bool,
    output_format: str,
) -> ValidationResult:
    """Run admission checks; short-circuit on first failure."""
    if op not in _VALID_OPS:
        return _reject("unknown_op", f"unsupported op: {op!r}")

    if not cwd or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return _reject(
            "cwd_missing", f"cwd must be an existing absolute directory: {cwd!r}"
        )

    try:
        subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError:
        return _reject(
            "not_a_git_repo", f"cwd is not inside a git working tree: {cwd!r}"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _reject("git_unreachable", f"git invocation failed for {cwd!r}: {exc}")

    try:
        status_proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        return _reject("git_unreachable", f"git status failed for {cwd!r}: {exc}")

    git_status_pre = status_proc.stdout
    dirty = bool(git_status_pre.strip())
    # mode-split: edit needs a clean baseline to produce a meaningful diff;
    # read_only is a scan and admits any tree state with audit_incomplete.
    if dirty and mode == "edit":
        return _reject(
            "working_tree_dirty",
            "working tree must be clean at admission for edit mode — "
            "stash or commit in-flight changes",
        )

    if session_id is not None and continue_recent:
        return _reject(
            "session_conflict",
            "session_id and continue_recent are mutually exclusive",
        )

    if output_format not in _VALID_OUTPUT_FORMATS:
        return _reject(
            "bad_output_format",
            f"output_format must be json or streaming-json, got {output_format!r}",
        )

    grok_path = _resolve_grok_path()
    if not grok_path:
        return _reject("grok_not_in_path", "grok executable not found on PATH")

    if not _grok_models_ok():
        return _reject(
            "missing_grok_auth",
            "grok models preflight failed — run grok login",
        )

    try:
        _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as exc:
        return _reject("sidecar_unavailable", str(exc))

    return ValidationResult(
        ok=True,
        reason="",
        reason_code="",
        permission_mode=_PERMISSION_BY_MODE[mode],
        grok_path=grok_path,
        git_status_pre=git_status_pre,
        dirty_admission=dirty,
    )
