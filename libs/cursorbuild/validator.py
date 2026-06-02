"""Admission validation for cursorbuild dispatch."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

from cursorbuild.constants import (
    CURSOR_AGENT_BIN,
    DEFAULT_READ_ONLY_MODE,
    FORBIDDEN_ARGV_TOKENS,
    OUTPUT_FORMAT,
    READ_ONLY_MODES,
    _SIDECAR_DIR,
    _VALID_TIERS,
    DEFAULT_TIMEOUT_SECONDS,
)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    reason: str
    reason_code: str
    cursor_agent_bin: str = ""
    git_status_pre: str = ""
    dirty_admission: bool = False
    read_only_mode: str = DEFAULT_READ_ONLY_MODE


def _reject(reason_code: str, reason: str) -> ValidationResult:
    return ValidationResult(ok=False, reason=reason, reason_code=reason_code)


@functools.lru_cache(maxsize=1)
def _resolve_cursor_agent_bin() -> str | None:
    return shutil.which(CURSOR_AGENT_BIN)


def _reset_cursor_agent_cache_for_tests() -> None:
    _resolve_cursor_agent_bin.cache_clear()


def _git_status_pre(cwd: str) -> tuple[str, bool]:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return "", True
    status = proc.stdout
    return status, bool(status.strip())


def validate_dispatch(  # noqa: PLR0911
    *,
    cwd: str,
    mode: Literal["read_only", "edit"],
    session_id: str | None,
    continue_session: bool,
    tier: str,
    timeout_seconds: int | None,
    read_only_mode: str,
    mcp_enabled: bool,
    prompt: str,
) -> ValidationResult:
    if tier not in _VALID_TIERS:
        return _reject(
            "bad_tier",
            f"tier must be one of {sorted(_VALID_TIERS)!r}, got {tier!r}",
        )
    if read_only_mode not in READ_ONLY_MODES:
        return _reject(
            "bad_read_only_mode",
            f"read_only_mode must be one of {sorted(READ_ONLY_MODES)!r}, "
            f"got {read_only_mode!r}",
        )
    if continue_session and session_id:
        return _reject(
            "resume_conflict",
            "session_id and continue_session are mutually exclusive",
        )
    if timeout_seconds is not None and timeout_seconds < 0:
        return _reject("bad_timeout", "timeout_seconds must be >= 0")
    if timeout_seconds is not None and timeout_seconds > 86_400:
        return _reject("bad_timeout", "timeout_seconds must be <= 86400")
    bin_path = _resolve_cursor_agent_bin()
    if not bin_path:
        return _reject(
            "cursor_agent_missing",
            f"{CURSOR_AGENT_BIN!r} not found on PATH",
        )
    if not os.path.isdir(cwd):
        return _reject("bad_cwd", f"cwd is not a directory: {cwd!r}")
    for token in FORBIDDEN_ARGV_TOKENS:
        if token in prompt:
            return _reject(
                "forbidden_prompt_token",
                f"prompt must not contain forbidden argv token {token!r}",
            )
    try:
        _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _reject("sidecar_unwritable", f"cannot create sidecar dir: {exc}")

    git_pre, dirty = _git_status_pre(cwd)
    if mode == "edit" and dirty:
        return _reject(
            "dirty_cwd",
            "edit mode requires a clean working tree at admission",
        )
    if mode == "read_only" and dirty:
        return ValidationResult(
            ok=True,
            reason="",
            reason_code="",
            cursor_agent_bin=bin_path,
            git_status_pre=git_pre,
            dirty_admission=True,
            read_only_mode=read_only_mode,
        )
    return ValidationResult(
        ok=True,
        reason="",
        reason_code="",
        cursor_agent_bin=bin_path,
        git_status_pre=git_pre,
        dirty_admission=False,
        read_only_mode=read_only_mode,
    )


def default_timeout_if_none(timeout_seconds: int | None) -> int | None:
    if timeout_seconds is None:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout_seconds
