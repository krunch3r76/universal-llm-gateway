"""cdp-ask remote lifecycle — SSH control on the CDP host from master manage."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..service_config import cdp_ask_url_config, resolve_cdp_ask_remote_target

_SERVICE_NAME = "cdp-ask"
# Shared NFS checkout on Jupiter (preferred over ~/universal-llm-gateway copy).
_REMOTE_REPO = "/mnt/torus/projects/universal-llm-gateway"
_REMOTE_FILES_ROOT = "/mnt/torus/mcp-data/files"
_CDP_ASK_PATHS = (
    "libs/cdp_ask/",
    "scripts/cdp-ask",
    "services/cdp-ask/",
)


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
        ssh_target,
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out = await proc.communicate()
    text = out[0].decode(errors="replace") if out[0] else ""
    return proc.returncode or 0, text.strip()


def _port() -> int:
    cfg = cdp_ask_url_config()
    return cfg[1] if cfg else 8770


async def start_cdp_ask_remote(root: Path) -> str:  # noqa: ARG001
    """Start cdp-ask on the remote CDP host from the shared NFS checkout."""
    port = _port()
    cmd = (
        f"mkdir -p /tmp/logs/cdp-ask ~/.gateway && "
        f"REPO={_REMOTE_REPO} && "
        f"test -f \"$REPO/scripts/cdp-ask\" && "
        f"nohup env CORTEX_FILES_ROOT={_REMOTE_FILES_ROOT} "
        f"\"$HOME/.venvs/universal/bin/python\" \"$REPO/scripts/cdp-ask\" "
        f"--port {port} >/tmp/logs/cdp-ask/remote-start.log 2>&1 & "
        f"echo $! > ~/.gateway/cdp-ask.pid && echo started"
    )
    code, text = await _run_ssh(cmd)
    if code == 0:
        return f"{_SERVICE_NAME} remote start ok.\n{text}"
    return f"{_SERVICE_NAME} remote start failed.\n{text}"


async def stop_cdp_ask_remote(root: Path) -> str:  # noqa: ARG001
    port = _port()
    cmd = (
        "if test -f ~/.gateway/cdp-ask.pid; then "
        "kill $(cat ~/.gateway/cdp-ask.pid) 2>/dev/null || true; "
        "rm -f ~/.gateway/cdp-ask.pid; "
        "fi; "
        f"fuser -k {port}/tcp 2>/dev/null || true; "
        "echo stopped"
    )
    code, text = await _run_ssh(cmd)
    if code == 0:
        return f"{_SERVICE_NAME} remote stop ok.\n{text}"
    return f"{_SERVICE_NAME} remote stop failed.\n{text}"


async def restart_cdp_ask_remote(root: Path) -> str:
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

    nfs_code, nfs_text = await _run_ssh(f"test -f {_REMOTE_REPO}/scripts/cdp-ask && echo nfs_ok")
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
            "ssh -o BatchMode=yes",
            src_arg,
            dest,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out = await proc.communicate()
        text = out[0].decode(errors="replace") if out[0] else ""
        if proc.returncode != 0:
            return (
                f"{_SERVICE_NAME} remote rsync failed for {rel} "
                f"(exit {proc.returncode}).\n{text}"
            )
    restart_msg = await restart_cdp_ask_remote(root)
    return f"{_SERVICE_NAME} synced+restarted on {address}.\n{restart_msg}"
