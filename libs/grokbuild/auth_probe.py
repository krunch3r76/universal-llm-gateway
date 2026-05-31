"""Standalone grok-auth probe with warmup and singleton cache.

∀ probe call: env = _build_env() + real HOME injected so grok finds
auth.json at HOME/.grok/auth.json.  _build_env() strips HOME intentionally
for dispatch subprocesses (runner_home.py sets it per-dispatch); the
standalone probe must re-inject the real HOME or grok exits non-zero
falsely indicating auth failure.

Warmup: two consecutive ``grok models`` calls; cold auth state may fail
on the first call; second confirms.  True result is cached in the module-
level singleton ``_PROBE``; False/EXPIRED is never cached so transient
failures do not poison all subsequent dispatches until restart.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum

from universal_logging import get_logger

from grokbuild.runner import _build_env

logger = get_logger(__name__)

_PROBE_TIMEOUT_S = 30
_WARMUP_CALLS = 2
_WARMUP_SLEEP_S = 0.5


class AuthStatus(StrEnum):
    OK = "ok"
    EXPIRED = "expired"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class AuthProbeResult:
    status: AuthStatus
    detail: str = ""


def _probe_env() -> dict[str, str]:
    """_build_env() + real HOME so grok can resolve HOME/.grok/auth.json."""
    env = _build_env()
    real_home = os.environ.get("HOME", "")
    if real_home:
        env["HOME"] = real_home
    return env


def probe_grok_auth() -> AuthProbeResult:
    """Run warmup grok-auth probe; return AuthProbeResult.

    Executes ``grok models`` twice (warmup) to avoid cold-start false
    negatives.  EXPIRED when grok exits non-zero.  MISSING when grok
    binary absent, timeout, or OSError.
    """
    env = _probe_env()
    last: AuthProbeResult | None = None
    for attempt in range(_WARMUP_CALLS):
        try:
            proc = subprocess.run(
                ["grok", "models"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                env=env,
            )
        except FileNotFoundError:
            return AuthProbeResult(AuthStatus.MISSING, "grok binary not found on PATH")
        except subprocess.TimeoutExpired:
            return AuthProbeResult(
                AuthStatus.MISSING,
                f"grok models timed out after {_PROBE_TIMEOUT_S}s",
            )
        except OSError as exc:
            return AuthProbeResult(AuthStatus.MISSING, f"grok models OSError: {exc}")

        if proc.returncode == 0:
            last = AuthProbeResult(AuthStatus.OK)
        else:
            stderr_snip = proc.stderr.strip()[:120]
            last = AuthProbeResult(
                AuthStatus.EXPIRED,
                f"exit {proc.returncode} (attempt {attempt + 1}/{_WARMUP_CALLS}): {stderr_snip}",
            )
            if attempt < _WARMUP_CALLS - 1:
                time.sleep(_WARMUP_SLEEP_S)
                continue
    return last or AuthProbeResult(AuthStatus.MISSING, "probe loop produced no result")


class _CachedAuthProbe:
    """Cache-True singleton for the validator dispatch path.

    ∀ ok() call: if _cached_ok is True, return immediately.
    Otherwise call probe_grok_auth(); cache True and return True only
    when status==OK; return False on EXPIRED/MISSING (do not cache).
    """

    __slots__ = ("_cached_ok",)

    def __init__(self) -> None:
        self._cached_ok: bool = False

    def ok(self) -> bool:
        if self._cached_ok:
            return True
        result = probe_grok_auth()
        if result.status == AuthStatus.OK:
            self._cached_ok = True
            return True
        return False

    def reset(self) -> None:
        """TEST-ONLY: clear cached True state."""
        self._cached_ok = False


_PROBE = _CachedAuthProbe()
