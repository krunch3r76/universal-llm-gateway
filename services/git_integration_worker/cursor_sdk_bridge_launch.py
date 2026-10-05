"""Cursor-sdk bridge launch adapter — owns SDK-version-coupled launch facts.

In-memory idempotency registry state is lost on worker restart (Phase 1 scope).

Per-dispatch HOME isolation: each dispatch seeds a private HOME with copied
``cli-config.json`` (identity), XDG ``auth.json`` (credential), and user-layer
Cursor settings for ``setting_sources=all``. That HOME, the repo venv, the
dispatch stamp, and the git identity reach the bridge through its **argv**:
``launch_sdk_bridge`` hands ``Client.launch_bridge`` a ``command`` list of the
form ``[/usr/bin/env, HOME=…, VIRTUAL_ENV=…, PATH=…, CURSOR_SDK_DISPATCH_ID=…,
GIT_*=…, NODE_OPTIONS=--require <preload>, CURSOR_SDK_SHELL_FALLBACK_CWD=<lane>,
CURSOR_SDK_SHELL_ALIGN_CWD=1 (lane B only), <bridge-bin>]``. The SDK forwards
the list verbatim as ``argv[0..n]``
and appends its own ``--workspace`` / ``--state-root`` / callback flags after
it; ``env(1)`` applies the assignments and execs the bridge in place, so the
``Popen`` the SDK holds *is* the bridge — its pid, its stderr, its environ.
The subprocess env the SDK builds (``os.environ`` + SDK defaults) is not
touched; GIW's own ``os.environ`` is never mutated; concurrent dispatches share
no state because the command list is a pure function of one call's arguments.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from cursor_sdk import Client
from cursor_sdk._vendor import resolve_bridge_path
from cursor_sdk.types import LocalAgentOptions
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    dispatch_ledger_env_vars,
)
from services.git_integration_worker.cursor_dispatch_playwright import (
    operator_playwright_browsers_dir,
)
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


# env(1) is the exec-in-place carrier for the per-dispatch overlay. It must
# be absolute: the PATH= assignment it applies is the dispatch PATH, not GIW's.
_ENV_BIN = "/usr/bin/env"


def resolve_bridge_bin() -> str:
    """Absolute path of the bridge launcher, resolved under GIW's own PATH.

    ``CURSOR_SDK_BRIDGE_BIN`` wins when set; otherwise the SDK's own resolver
    (bundled launcher, then PATH). Made absolute with ``os.path.abspath`` —
    never ``resolve()`` — because the bundled launcher finds its ``node``
    through ``$0``'s directory and a followed symlink would move that.
    Raises ``cursor_sdk.errors.CursorSDKError`` when nothing resolves.
    """
    raw = _SDK_BRIDGE_BIN or resolve_bridge_path()
    return os.path.abspath(raw)


def shell_cwd_preload_path() -> Path:
    """Absolute preload beside this module — the worker checkout, not the lane."""
    return Path(__file__).resolve().parent / "cursor_sdk_shell_cwd_preload.cjs"


def build_bridge_command(
    *,
    bridge_bin: str,
    dispatch_home: Path,
    repo_venv: Path | None,
    real_home: Path | str | None,
    dispatch_id: str | None,
    lane_path: Path | str | None = None,
    align_shell_cwd: bool = False,
) -> list[str]:
    """``Client.launch_bridge(command=)`` argv: ``env(1)`` assignments then the bridge.

    The SDK appends ``--workspace``/``--state-root``/callback flags after the
    last element, so the bridge binary must be last and must be safe as an
    ``env(1)`` operand: absolute (the ``PATH=`` assignment is applied before
    the exec, so a bare name would be searched under the dispatch PATH), no
    ``=`` (would parse as an assignment), not ``-``-prefixed (would parse as
    an option). ``VIRTUAL_ENV``/``PATH`` are omitted when ``repo_venv`` is
    None; ``CURSOR_SDK_DISPATCH_ID``/``GIT_*`` when ``dispatch_id`` is None —
    the bridge then inherits GIW's values for those keys, unchanged. PATH is
    always complete: the dispatch prepend, then GIW's PATH when non-empty.
    ``PLAYWRIGHT_BROWSERS_PATH`` is set when the operator browser cache exists
    under ``real_home`` so resume of pre-seed homes (symlink skipped) still
    finds chromium (a:37291). When ``lane_path`` is set, ``NODE_OPTIONS``
    (``--require`` of the shell-cwd preload, in front of any ``NODE_OPTIONS``
    already in ``os.environ``) and ``CURSOR_SDK_SHELL_FALLBACK_CWD`` are
    assigned just before the binary. ``CURSOR_SDK_SHELL_ALIGN_CWD=1`` is
    appended only when ``align_shell_cwd`` is true (lane B). The preload
    chdirs and fills an empty bash cwd only then; the missing-directory
    rewrite stays armed for every lane. Pinned ``Popen`` still has no ``cwd=``.
    Reads ``os.environ["PATH"]`` and ``os.environ["NODE_OPTIONS"]`` and stats
    ``<real_home>/.cache/ms-playwright``; writes nothing.
    """
    if not os.path.isabs(bridge_bin) or "=" in bridge_bin or bridge_bin.startswith("-"):
        raise ValueError(
            "bridge_bin must be an absolute path without '=' and not '-'-prefixed: "
            f"{bridge_bin!r}"
        )
    command = [_ENV_BIN, f"HOME={dispatch_home}"]
    browsers = operator_playwright_browsers_dir(real_home)
    if browsers is not None:
        command.append(f"PLAYWRIGHT_BROWSERS_PATH={browsers}")
    if repo_venv is not None:
        prepend = build_dispatch_path_prepend(repo_venv, real_home=real_home)
        base = os.environ.get("PATH", "")
        path_value = f"{prepend}{os.pathsep}{base}" if base else prepend
        command.append(f"VIRTUAL_ENV={repo_venv}")
        command.append(f"PATH={path_value}")
    if dispatch_id is not None:
        command.append(f"CURSOR_SDK_DISPATCH_ID={dispatch_id}")
        command.extend(
            f"{k}={v}" for k, v in dispatch_git_env_vars(dispatch_id).items()
        )
        command.extend(f"{k}={v}" for k, v in dispatch_ledger_env_vars().items())
    if lane_path is not None:
        preload = shell_cwd_preload_path()
        prior = os.environ.get("NODE_OPTIONS", "").strip()
        require = f"--require {preload}"
        node_options = f"{require} {prior}".strip()
        command.append(f"NODE_OPTIONS={node_options}")
        command.append(f"CURSOR_SDK_SHELL_FALLBACK_CWD={lane_path}")
        if align_shell_cwd:
            command.append("CURSOR_SDK_SHELL_ALIGN_CWD=1")
    command.append(bridge_bin)
    return command


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
    """Launch the cursor-sdk bridge for one dispatch with bounded retry.

    Builds the ``env(1)`` argv shim once (``build_bridge_command``) and passes
    it as ``Client.launch_bridge(command=)``; the SDK appends its own flags
    and execs it, so the bridge runs under the dispatch HOME, venv, PATH,
    stamp, and git identity while GIW's ``os.environ`` is untouched. Bridge
    resolution and command validation raise before attempt 1.

    Retries are bounded by ``_SDK_LAUNCH_ATTEMPTS`` and only re-attempt the
    pre-discovery transient (empty tool-callback token before discovery). A
    launch timeout fails on attempt 1 and does not consume the retry ladder.

    Reads ``ctx.dispatch_id``, ``ctx.dispatch_workspace``, and ``ctx.lane``
    (alignment only when lane is ``B``). Other context fields are the
    caller's responsibility.

    Lane-B: refuse spawn when the worktree is gone (friction 37813) so
    Node does not die ``spawn_enoent_missing_cwd``.
    """
    from services.git_integration_worker.cursor_sdk_worktree_remint import (
        ensure_spawn_workspace,
    )

    ctx = ensure_spawn_workspace(ctx)
    bridge_bin = resolve_bridge_bin()
    command = build_bridge_command(
        bridge_bin=bridge_bin,
        dispatch_home=dispatch_home,
        repo_venv=repo_venv,
        real_home=real_home,
        dispatch_id=ctx.dispatch_id,
        lane_path=ctx.dispatch_workspace,
        align_shell_cwd=ctx.lane == "B",
    )
    logger.info(
        "cursor sdk bridge launch: dispatch_id=%s bridge_bin=%s",
        ctx.dispatch_id,
        bridge_bin,
    )
    client = None
    for attempt in range(_SDK_LAUNCH_ATTEMPTS):
        try:
            client = Client.launch_bridge(
                command=command,
                workspace=str(ctx.dispatch_workspace),
                state_root=str(bridge_state),
                timeout=_SDK_LAUNCH_TIMEOUT_S,
                # Friction 23057: without this the SDK's default
                # 600s stream read timeout kills long silent tool
                # legs despite healthy heartbeats.
                client_timeout=client_timeout,
                local=local,
            )
            from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
                reset_occupancy_cache,
            )

            reset_occupancy_cache()
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
