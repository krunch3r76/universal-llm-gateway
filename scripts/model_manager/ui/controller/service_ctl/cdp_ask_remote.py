"""cdp-ask remote lifecycle — SSH start/stop/restart on the CDP host from master manage.

Exports hub ``EVENTS_INGEST_TCP`` on remote start so Jupiter followup observation
events reach the hub Event Service (UDS is local-only on the satellite).

Also exports ``ULG_CODE_VERSION`` from the shared NFS checkout HEAD at start so
``resolve_code_version`` seals an attributable SHA instead of falling through to
``unknown`` (no deploy stamp on the satellite host process).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from pathlib import Path

from universal_logging import get_logger

from ..service_config import (
    cdp_ask_url_config,
    load_event_service_config,
    resolve_cdp_ask_remote_target,
)

logger = get_logger(__name__)

_SERVICE_NAME = "cdp-ask"
# Shared NFS checkout on Jupiter (preferred over ~/universal-llm-gateway copy).
_REMOTE_REPO = "/mnt/torus/projects/universal-llm-gateway"
_REMOTE_FILES_ROOT = "/mnt/torus/mcp-data/files"
_CDP_ASK_PATHS = (
    "libs/cdp_ask/",
    "scripts/cdp-ask",
    "services/cdp-ask/",
)
# Bound remote lifecycle SSH so a stuck session cannot pin fleet_deploy forever.
_SSH_TIMEOUT_S = 30.0
_DEFAULT_INGEST_TCP_PORT = 7101


def _hub_ingest_tcp_port() -> int:
    """Port for hub Event Service TCP ingest (yaml / env / 7101)."""
    cfg = load_event_service_config()
    if cfg is not None:
        return int(cfg.tcp_ingest_port)
    raw = os.environ.get("EVENT_INGEST_TCP_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_INGEST_TCP_PORT


def _hub_lan_ipv4() -> str:
    """Outbound IPv4 of this manage host — Jupiter has no hub UDS; needs LAN reachability."""
    with contextlib.suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    with contextlib.suppress(OSError):
        return socket.gethostname()
    return "127.0.0.1"


def resolve_hub_events_ingest_tcp() -> str:
    """Resolve ``host:port`` for remote cdp-ask → hub Event Service TCP ingest.

    Order: ``EVENTS_INGEST_TCP`` ≺ ``EVENT_SERVICE_INGEST_HOST``/``EVENTS_INGEST_HOST``
    + port ≺ this manage host's LAN IPv4 — avoid hardcoding a fleet IP.
    """
    explicit = os.environ.get("EVENTS_INGEST_TCP", "").strip()
    if explicit:
        return explicit
    host = (
        os.environ.get("EVENT_SERVICE_INGEST_HOST", "").strip()
        or os.environ.get("EVENTS_INGEST_HOST", "").strip()
    )
    port_s = os.environ.get("EVENTS_INGEST_PORT", "").strip()
    port = int(port_s) if port_s.isdigit() else _hub_ingest_tcp_port()
    if not host:
        host = _hub_lan_ipv4()
    return f"{host}:{port}"


def _ssh_target() -> tuple[str, str] | None:
    cfg = cdp_ask_url_config()
    if cfg is None:
        return None
    host, _port, _base = cfg
    resolved = resolve_cdp_ask_remote_target(host)
    if resolved is None:
        return None
    _hostname, address, ssh_user = resolved
    return f"{ssh_user}@{address}", address


async def _run_ssh(command: str) -> tuple[int, str]:
    target = _ssh_target()
    if target is None:
        return 1, f"{_SERVICE_NAME} remote target could not be resolved."
    ssh_target, _address = target
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        ssh_target,
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_SSH_TIMEOUT_S)
    except TimeoutError:
        logger.warning(
            "%s remote ssh timed out after %.0fs; killing local ssh",
            _SERVICE_NAME,
            _SSH_TIMEOUT_S,
        )
        proc.kill()
        try:
            await proc.communicate()
        except Exception:  # noqa: BLE001 — best-effort reap after kill
            pass
        return 1, f"{_SERVICE_NAME} remote ssh timed out after {_SSH_TIMEOUT_S:.0f}s."
    text = out.decode(errors="replace") if out else ""
    return proc.returncode or 0, text.strip()


def _port() -> int:
    cfg = cdp_ask_url_config()
    return cfg[1] if cfg else 8770


async def start_cdp_ask_remote(root: Path) -> str:  # noqa: ARG001
    """Start cdp-ask on the remote CDP host from the shared NFS checkout.

    Writes ``~/.gateway/cdp-ask.env`` (incl. ``ULG_REPO``) then starts the
    user unit via ``systemctl --user`` so Type=forking owns the MainPID cgroup
    (direct script invoke left the unit inactive / failed on adopt).
    """
    port = _port()
    ingest_tcp = resolve_hub_events_ingest_tcp()
    cmd = (
        "mkdir -p /tmp/logs/cdp-ask ~/.gateway; "
        f"REPO={_REMOTE_REPO}; "
        'test -f "$REPO/scripts/cdp-ask-start" || exit 1; '
        'ULG_CODE_VERSION=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true); '
        f"cat > ~/.gateway/cdp-ask.env <<EOF\n"
        f"EVENTS_INGEST_TCP={ingest_tcp}\n"
        f"CORTEX_FILES_ROOT={_REMOTE_FILES_ROOT}\n"
        "ULG_CODE_VERSION=$ULG_CODE_VERSION\n"
        "ULG_REPO=$REPO\n"
        f"PORT={port}\n"
        "EOF\n"
        # Unit ExecStart → scripts/cdp-ask-start (setsid under INVOCATION_ID).
        "systemctl --user reset-failed cdp-ask.service 2>/dev/null || true; "
        "systemctl --user start cdp-ask.service; "
        "systemctl --user is-active cdp-ask.service; "
        "echo started"
    )
    code, text = await _run_ssh(cmd)
    if code == 0:
        return f"{_SERVICE_NAME} remote start ok.\n{text}"
    return f"{_SERVICE_NAME} remote start failed.\n{text}"


async def stop_cdp_ask_remote(root: Path) -> str:  # noqa: ARG001
    """Stop remote cdp-ask via systemctl kill, then pidfile/port fallback."""
    port = _port()
    cmd = (
        # RefuseManualStop blocks `systemctl stop`; kill still reaches MainPID.
        "systemctl --user kill -s SIGTERM cdp-ask.service 2>/dev/null || true; "
        "sleep 0.5; "
        "if test -f ~/.gateway/cdp-ask.pid; then "
        "kill $(cat ~/.gateway/cdp-ask.pid) 2>/dev/null || true; "
        "rm -f ~/.gateway/cdp-ask.pid; "
        "fi; "
        f"fuser -k {port}/tcp 2>/dev/null || true; "
        "systemctl --user reset-failed cdp-ask.service 2>/dev/null || true; "
        "echo stopped"
    )
    code, text = await _run_ssh(cmd)
    if code == 0:
        return f"{_SERVICE_NAME} remote stop ok.\n{text}"
    return f"{_SERVICE_NAME} remote stop failed.\n{text}"


async def restart_cdp_ask_remote(root: Path) -> str:
    """Stop then start remote cdp-ask; concatenates both SSH status messages."""
    stop_msg = await stop_cdp_ask_remote(root)
    await asyncio.sleep(1.0)
    start_msg = await start_cdp_ask_remote(root)
    return f"{stop_msg}\n{start_msg}"


async def sync_restart_cdp_ask_remote(root: Path) -> str:
    """Restart remote cdp-ask.

    Prefer shared NFS checkout (no rsync). Fall back to per-path rsync into
    ``~/universal-llm-gateway`` only when the NFS repo is absent remotely.
    """
    target = _ssh_target()
    if target is None:
        return f"{_SERVICE_NAME} remote target could not be resolved."
    _ssh_user_host, address = target

    nfs_code, nfs_text = await _run_ssh(
        f"test -f {_REMOTE_REPO}/scripts/cdp-ask-start && echo nfs_ok"
    )
    if nfs_code == 0 and "nfs_ok" in nfs_text:
        restart_msg = await restart_cdp_ask_remote(root)
        return f"{_SERVICE_NAME} restarted on {address} (shared NFS).\n{restart_msg}"

    # Non-NFS fallback: rsync into home checkout then restart (home path).
    ssh_target, _ = target
    for rel in _CDP_ASK_PATHS:
        src = root / rel
        if not src.exists():
            continue
        if src.is_file():
            src_arg = str(src)
            dest = f"{ssh_target}:~/universal-llm-gateway/{rel}"
        else:
            src_arg = f"{str(src).rstrip('/')}/"
            dest = f"{ssh_target}:~/universal-llm-gateway/{rel.rstrip('/')}/"
        proc = await asyncio.create_subprocess_exec(
            "rsync",
            "-az",
            "--delete",
            "-e",
            "ssh -o BatchMode=yes -o ConnectTimeout=10",
            src_arg,
            dest,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_SSH_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            try:
                await proc.communicate()
            except Exception:  # noqa: BLE001 — best-effort reap after kill
                pass
            return (
                f"{_SERVICE_NAME} remote rsync timed out for {rel} "
                f"after {_SSH_TIMEOUT_S:.0f}s."
            )
        text = out.decode(errors="replace") if out else ""
        if proc.returncode != 0:
            return (
                f"{_SERVICE_NAME} remote rsync failed for {rel} "
                f"(exit {proc.returncode}).\n{text}"
            )
    restart_msg = await restart_cdp_ask_remote(root)
    return f"{_SERVICE_NAME} synced+restarted on {address}.\n{restart_msg}"
