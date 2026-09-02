"""Cursor-sdk bridge launch adapter — owns SDK-version-coupled launch facts.

In-memory idempotency registry state is lost on worker restart (Phase 1 scope).

Phase 2 HOME isolation (T2b 2026-06-11, thread 1559): each dispatch seeds a
private HOME with copied ``cli-config.json`` (identity), XDG ``auth.json``
(credential), and user-layer Cursor settings for ``setting_sources=all``.
``Client.launch_bridge`` snapshots ``os.environ`` at ``Popen`` (no ``env=``
kwarg in cursor-sdk 0.1.8). Each dispatch records its HOME/venv override in
thread-local storage; a monkeypatch on ``_bridge_subprocess_env`` overlays it
into the bridge subprocess env at ``Popen`` time. The override is confined to
the dispatch's own worker thread, so concurrent and timed-out (orphan)
dispatches never race on shared global state and no dispatch lock is needed.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from cursor_sdk import Client
from cursor_sdk.types import LocalAgentOptions
from universal_logging import get_logger

from services.git_integration_worker.cursor_home import (
    build_dispatch_path_prepend,
    dispatch_git_env_vars,
)
from services.git_integration_worker.cursor_sdk_dispatch_context import (
    SdkDispatchContext,
)

logger = get_logger(__name__)

_SDK_BRIDGE_BIN = os.environ.get("CURSOR_SDK_BRIDGE_BIN", "").strip() or None

# Bounded retry for the pre-discovery bridge-launch transient: cursor-sdk
# intermittently exits the bridge BEFORE the discovery handshake with an empty
# "--tool-callback-auth-token" (the local callback token is momentarily
# unavailable at launch, e.g. while the cursor credential is mid-rotation).
# Pre-discovery => the agent never ran and nothing was written, so re-seeding the
# dispatch HOME (to pick up the rotated credential) and relaunching is
# side-effect-free and safe. Confirmed self-recovering 2026-06-15
# (79cc476a->e4afe1fe, 69eededb->0ae492ce); see cortex assertion 19136 /
# notes/system/threads/cursor-sdk-bridge-token-fix.md.
_SDK_LAUNCH_ATTEMPTS = max(1, int(os.environ.get("CURSOR_SDK_LAUNCH_ATTEMPTS", "3")))
_SDK_LAUNCH_BACKOFFS_S = (2.0, 5.0)

# Bridge handshake deadline — deliberately NOT _SDK_TIMEOUT_S. launch_bridge only
# spawns the bridge subprocess and completes discovery; the 1800s run/wait budget
# is for the agent's work. Sharing it meant a bridge that never armed could hold
# the exclusive write lease for half an hour before any deadline noticed, and the
# heartbeat that would reveal it does not start until launch returns
# (_start_heartbeat, below). Healthy launch-to-first-toolcall measures ~13s.
# A launch timeout is not a pre-discovery transient, so it fails on attempt 1
# rather than consuming the retry ladder.
_SDK_LAUNCH_TIMEOUT_S = float(os.environ.get("CURSOR_SDK_LAUNCH_TIMEOUT", "180"))

_PRE_DISCOVERY_TRANSIENT_MARKERS = ("before discovery", "--tool-callback-auth-token")


def _is_pre_discovery_transient(exc: BaseException) -> bool:
    """True iff exc is the safe-to-retry pre-discovery bridge launch transient."""
    msg = str(exc)
    return any(marker in msg for marker in _PRE_DISCOVERY_TRANSIENT_MARKERS)


_dispatch_env = threading.local()
_BRIDGE_ENV_PATCH_INSTALLED = False
_PATH_PREPEND_KEY = "__CURSOR_SDK_PATH_PREPEND__"


def _dispatch_env_overlay() -> dict[str, str] | None:
    return getattr(_dispatch_env, "overrides", None)


def _install_bridge_env_patch() -> None:
    """Overlay the per-dispatch HOME/venv onto the bridge subprocess env.

    cursor-sdk 0.1.8 ``Bridge.launch`` builds the subprocess env from
    ``_bridge_subprocess_env()`` (``dict(os.environ)`` + SDK setdefaults)
    synchronously on the caller thread before ``Popen``. We wrap that function
    so it overlays the calling thread's dispatch override (HOME, VIRTUAL_ENV,
    PATH-prepend) read from thread-local storage, without mutating
    process-global ``os.environ``. Idempotent; patches the sync module global
    and the async module's imported binding.
    """
    global _BRIDGE_ENV_PATCH_INSTALLED
    if _BRIDGE_ENV_PATCH_INSTALLED:
        return

    from cursor_sdk import _bridge as _sdk_bridge

    _orig_env = _sdk_bridge._bridge_subprocess_env

    def _bridge_subprocess_env_with_overlay() -> Mapping[str, str]:
        env = dict(_orig_env())
        overrides = _dispatch_env_overlay()
        if overrides:
            home = overrides.get("HOME")
            if home is not None:
                env["HOME"] = home
            venv = overrides.get("VIRTUAL_ENV")
            if venv is not None:
                env["VIRTUAL_ENV"] = venv
            prepend = overrides.get(_PATH_PREPEND_KEY)
            if prepend is not None:
                cur = env.get("PATH")
                env["PATH"] = f"{prepend}{os.pathsep}{cur}" if cur else prepend
            dispatch_id = overrides.get("CURSOR_SDK_DISPATCH_ID")
            if dispatch_id is not None:
                env["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
            for key, value in overrides.items():
                if key.startswith("GIT_"):
                    env[key] = value
        return env

    _sdk_bridge._bridge_subprocess_env = _bridge_subprocess_env_with_overlay
    try:
        from cursor_sdk import _async_bridge as _sdk_async_bridge

        _sdk_async_bridge._bridge_subprocess_env = _bridge_subprocess_env_with_overlay
    except Exception:  # async bridge optional; the worker uses the sync path
        logger.debug("cursor-sdk async bridge env patch skipped (module absent)")

    _BRIDGE_ENV_PATCH_INSTALLED = True
    logger.info(
        "cursor-sdk bridge subprocess-env patch installed "
        "(thread-confined HOME overlay; no os.environ mutation)"
    )


@contextmanager
def _dispatch_home_overlay(
    home: Path,
    *,
    repo_venv: Path | None = None,
    real_home: Path | str | None = None,
    dispatch_id: str | None = None,
):
    """Thread-confined HOME/venv overlay for one dispatch.

    Records the override in thread-local storage read by the patched
    ``_bridge_subprocess_env`` during ``Client.launch_bridge`` (same thread).
    No ``os.environ`` mutation and no lock: each dispatch runs in its own
    ``asyncio.to_thread`` worker thread, so overrides never collide and a
    timed-out orphan thread cannot leak HOME into a newly admitted dispatch.
    """
    overrides: dict[str, str] = {"HOME": str(home)}
    if dispatch_id is not None:
        overrides["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
        overrides.update(dispatch_git_env_vars(dispatch_id))
    if repo_venv is not None:
        overrides["VIRTUAL_ENV"] = str(repo_venv)
        overrides[_PATH_PREPEND_KEY] = build_dispatch_path_prepend(
            repo_venv, real_home=real_home
        )
    prev = getattr(_dispatch_env, "overrides", None)
    _dispatch_env.overrides = overrides
    try:
        yield
    finally:
        _dispatch_env.overrides = prev


_install_bridge_env_patch()


def launch_sdk_bridge(
    ctx: SdkDispatchContext,
    *,
    bridge_state: Path,
    dispatch_home: Path,
    repo_venv: Path,
    real_home: Path,
    local: LocalAgentOptions | Mapping[str, Any] | None,
    client_timeout: httpx.Timeout,
) -> Client:
    """Launch the cursor-sdk bridge with dispatch HOME overlay and bounded retry.

    Enters ``_dispatch_home_overlay`` only around the launch retry ladder so the
    thread-local override is visible when ``Client.launch_bridge`` calls the
    patched ``_bridge_subprocess_env`` at ``Popen`` time. At cursor-sdk 1.0.30
    that function is invoked only from ``Bridge.launch`` and ``AsyncBridge.launch``,
    both during launch, so narrowing the overlay lifetime to this call is safe.

    Retries are bounded by ``_SDK_LAUNCH_ATTEMPTS`` and only re-attempt the
    pre-discovery transient (empty tool-callback token before discovery). A launch
    timeout fails on attempt 1 and does not consume the retry ladder.

    Reads ``ctx.dispatch_id`` and ``ctx.dispatch_workspace`` only; other context
    fields are the caller's responsibility.
    """
    with _dispatch_home_overlay(
        dispatch_home,
        repo_venv=repo_venv,
        real_home=real_home,
        dispatch_id=ctx.dispatch_id,
    ):
        client = None
        for attempt in range(_SDK_LAUNCH_ATTEMPTS):
            try:
                client = Client.launch_bridge(
                    _SDK_BRIDGE_BIN,
                    workspace=str(ctx.dispatch_workspace),
                    state_root=str(bridge_state),
                    timeout=_SDK_LAUNCH_TIMEOUT_S,
                    # Friction 23057: without this the SDK's default
                    # 600s stream read timeout kills long silent tool
                    # legs despite healthy heartbeats.
                    client_timeout=client_timeout,
                    local=local,
                )
                break
            except Exception as launch_exc:  # noqa: BLE001
                is_last = attempt + 1 >= _SDK_LAUNCH_ATTEMPTS
                if is_last or not _is_pre_discovery_transient(launch_exc):
                    raise
                backoff = _SDK_LAUNCH_BACKOFFS_S[
                    min(attempt, len(_SDK_LAUNCH_BACKOFFS_S) - 1)
                ]
                logger.warning(
                    "cursor sdk bridge pre-discovery transient: "
                    "dispatch_id=%s attempt=%d/%d err=%s; retrying in %.1fs",
                    ctx.dispatch_id,
                    attempt + 1,
                    _SDK_LAUNCH_ATTEMPTS,
                    launch_exc,
                    backoff,
                )
                time.sleep(backoff)
    return client
