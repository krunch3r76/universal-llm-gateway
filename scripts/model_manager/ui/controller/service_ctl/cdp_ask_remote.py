"""cdp-ask remote lifecycle — SSH control on the CDP host from master manage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..service_config import cdp_ask_url_config, resolve_cdp_ask_remote_target

_SERVICE_NAME = "cdp-ask"
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


def _manage_rpc(action: str) -> str:
    payload = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": action,
                "params": {"service": "cdp_ask"},
                "id": 1,
            }
        )
        + "\n"
    )
    return (
        "python3 - <<'PY'\n"
        "import json, socket\n"
        "from transport_utils import MANAGE_SOCKET\n"
        f"req = {payload!r}\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "s.connect(MANAGE_SOCKET)\n"
        "s.sendall(req.encode())\n"
        "resp = s.recv(65536).decode()\n"
        "print(resp.strip())\n"
        "data = json.loads(resp)\n"
        "raise SystemExit(0 if 'error' not in data else 1)\n"
        "PY"
    )


async def start_cdp_ask_remote(root: Path) -> str:  # noqa: ARG001
    """Start cdp-ask on the remote CDP host via manage.sock, script fallback."""
    code, text = await _run_ssh(
        "cd ~/universal-llm-gateway && "
        f"({_manage_rpc('start')} "
        "|| (test -x scripts/cdp-ask && ./scripts/cdp-ask --port 8770 >/tmp/logs/cdp-ask/remote-start.log 2>&1 & echo started))"
    )
    if code == 0:
        return f"{_SERVICE_NAME} remote start ok.\n{text}"
    return f"{_SERVICE_NAME} remote start failed.\n{text}"


async def stop_cdp_ask_remote(root: Path) -> str:  # noqa: ARG001
    code, text = await _run_ssh(
        "cd ~/universal-llm-gateway && "
        f"({_manage_rpc('stop')} "
        "|| (test -f ~/.gateway/cdp-ask.pid && kill $(cat ~/.gateway/cdp-ask.pid) && rm -f ~/.gateway/cdp-ask.pid && echo stopped))"
    )
    if code == 0:
        return f"{_SERVICE_NAME} remote stop ok.\n{text}"
    return f"{_SERVICE_NAME} remote stop failed.\n{text}"


async def restart_cdp_ask_remote(root: Path) -> str:
    stop_msg = await stop_cdp_ask_remote(root)
    start_msg = await start_cdp_ask_remote(root)
    return f"{stop_msg}\n{start_msg}"


async def sync_restart_cdp_ask_remote(root: Path) -> str:
    target = _ssh_target()
    if target is None:
        return f"{_SERVICE_NAME} remote target could not be resolved."
    ssh_target, address = target
    dest = f"{ssh_target}:~/universal-llm-gateway/"
    rsync_args = ["rsync", "-az", "--delete"]
    for rel in _CDP_ASK_PATHS:
        rsync_args.append(str(root / rel))
        rsync_args.append(f"{dest}{rel.rstrip('/')}/")
    proc = await asyncio.create_subprocess_exec(
        *rsync_args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out = await proc.communicate()
    rsync_text = out[0].decode(errors="replace") if out[0] else ""
    if proc.returncode != 0:
        return f"{_SERVICE_NAME} remote rsync failed (exit {proc.returncode}).\n{rsync_text}"
    restart_msg = await restart_cdp_ask_remote(root)
    return (
        f"{_SERVICE_NAME} synced to {address}.\n{rsync_text}\n{restart_msg}".strip()
    )
