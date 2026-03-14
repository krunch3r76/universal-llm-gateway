"""Topology management for federated remotes.

This module owns remote CRUD in ``~/.gateway/stargate.yaml``, emits node env files
for deployment, and provides async deploy/restart flows used by the TUI.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import yaml

logger = logging.getLogger(__name__)

_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_MASTER_CONFIG = _GATEWAY_DIR / "stargate.yaml"


class RemoteConfig(TypedDict):
    stargate_id: str
    url: str
    api_key: str


def list_remotes() -> list[RemoteConfig]:
    """Read remotes from ~/.gateway/stargate.yaml federation section."""
    if not _MASTER_CONFIG.exists():
        return []
    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    federation = data.get("federation", {})
    remotes_raw = federation.get("remotes") or []
    remotes: list[RemoteConfig] = []
    if not isinstance(remotes_raw, list):
        return remotes
    for remote in remotes_raw:
        if not isinstance(remote, dict):
            continue
        remotes.append(
            {
                "stargate_id": str(remote.get("stargate_id", "")),
                "url": str(remote.get("url", "")),
                "api_key": str(remote.get("api_key", "")),
            }
        )
    return remotes


def get_master_port() -> int:
    """Read the master Stargate port from config."""
    if not _MASTER_CONFIG.exists():
        return 9999
    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    return int(data.get("proxy", {}).get("port", 9999))


def add_remote(
    *,
    hostname: str,
    address: str,
    model_path: str,
    ssh_user: str,
) -> dict[str, str]:
    """Add a remote node to the Master Stargate config and generate node env.

    Generates federation keys, writes ~/.gateway/nodes/<hostname>.env,
    and appends a remote entry to ~/.gateway/stargate.yaml.

    Args:
        hostname: Node identifier (e.g., "jupiter")
        address: Network address (e.g., "jupiter", "192.168.1.50")
        model_path: Host path to models on the remote machine
        ssh_user: SSH login user on the remote (e.g., "krunch3r")

    Returns:
        Dict with generated keys for display to user:
          relay_key, edge_key, node_env_path
    """
    if not _MASTER_CONFIG.exists():
        raise FileNotFoundError(
            f"Master config not found: {_MASTER_CONFIG}\n"
            "Start the local setup first (Services → Start Stargate)."
        )

    relay_key = secrets.token_hex(24)
    edge_key = secrets.token_hex(24)

    _write_node_env(
        hostname=hostname,
        model_path=model_path,
        ssh_user=ssh_user,
        edge_key=edge_key,
        relay_key=relay_key,
        master_host=_get_local_address(),
    )

    _append_remote_to_config(
        hostname=hostname,
        address=address,
        relay_key=relay_key,
    )

    return {
        "relay_key": relay_key,
        "edge_key": edge_key,
        "node_env_path": str(_NODES_DIR / f"{hostname}.env"),
    }


def remove_remote(hostname: str) -> bool:
    """Remove a remote node from Master Stargate config.

    Does NOT delete the node env file (user may want to re-add).

    Returns:
        True if removed, False if not found.
    """
    if not _MASTER_CONFIG.exists():
        return False

    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    federation = data.get("federation", {})
    remotes: list[dict[str, Any]] = federation.get("remotes") or []

    relay_id = f"relay-{hostname}"
    original_len = len(remotes)
    remotes = [r for r in remotes if r.get("stargate_id") != relay_id]

    if len(remotes) == original_len:
        return False

    federation["remotes"] = remotes if remotes else []
    data["federation"] = federation
    _MASTER_CONFIG.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False)
    )
    logger.info("Removed remote %s from %s", relay_id, _MASTER_CONFIG)
    return True


async def deploy_remote(
    *,
    hostname: str,
    address: str,
    workspace_root: Path,
    build: bool = False,
    restart: bool = False,
    scope: str = "all",
) -> AsyncIterator[str]:
    """Deploy relay+edge to a remote host via rsync + ssh.

    Yields log lines as they appear. Three steps:
    1. rsync repo (excludes local-only files)
    2. scp ~/.gateway/nodes/<hostname>.env
    3. ssh ./manage relay [--restart] [--build] [--scope SCOPE]
    """
    repo = workspace_root.resolve()
    node_env = _NODES_DIR / f"{hostname}.env"
    if not node_env.exists():
        yield f"[red]Node env not found: {node_env}. Add Remote first.[/red]"
        return

    ssh_user = _read_node_env_key(node_env, "SSH_USER")
    if not ssh_user:
        yield f"[red]SSH_USER missing in {node_env}. Re-add the remote.[/red]"
        return
    ssh_target = f"{ssh_user}@{address}"
    dest = f"{ssh_target}:~/universal-llm-gateway/"

    rsync_excludes = [
        ".env.local",
        "tmp/gpu-nodes",
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "*.pyc",
        # These directories are COPY'd into Docker builder stages (vllm-builder,
        # llama-builder, llama-server-builder). Syncing them invalidates the
        # vLLM CUDA build cache, forcing a full recompile (~1h). Remote deploys
        # are always application-only pushes, never builder script updates.
        "libs/inference_djinn/scripts/build/",
        "docker/scripts/build/",
    ]
    rsync_args = [
        "rsync",
        "-az",
        "--delete",
        *[f"--exclude={x}" for x in rsync_excludes],
        f"{repo}/",
        dest,
    ]
    yield f"$ {' '.join(rsync_args)}"
    proc = await asyncio.create_subprocess_exec(
        *rsync_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(repo),
    )
    if proc.stdout is None:
        yield "[red]rsync failed to stream output.[/red]"
        return
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip()
    code = await proc.wait()
    if code != 0:
        yield f"[red]rsync failed (exit {code}).[/red]"
        return

    scp_args = [
        "scp",
        str(node_env),
        f"{ssh_target}:~/.gateway/nodes/{hostname}.env",
    ]
    yield f"$ {' '.join(scp_args)}"
    proc = await asyncio.create_subprocess_exec(
        *scp_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc.stdout is None:
        yield "[red]scp failed to stream output.[/red]"
        return
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip()
    code = await proc.wait()
    if code != 0:
        yield f"[red]scp failed (exit {code}).[/red]"
        return

    relay_cmd = "./manage relay"
    if restart:
        relay_cmd += " --restart"
    if build:
        relay_cmd += f" --build --scope {scope}"
    ssh_args = [
        "ssh",
        "-t",
        "-o",
        "BatchMode=yes",
        ssh_target,
        f"cd ~/universal-llm-gateway && {relay_cmd}",
    ]
    yield f"$ ssh -t -o BatchMode=yes {ssh_target} 'cd ~/universal-llm-gateway && {relay_cmd}'"
    proc = await asyncio.create_subprocess_exec(
        *ssh_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc.stdout is None:
        yield "[red]ssh relay failed to stream output.[/red]"
        return
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip()
    code = await proc.wait()
    if code != 0:
        yield f"[red]ssh relay failed (exit {code}).[/red]"
        return


# ---------------------------------------------------------------------------
# Event-driven relay readiness
# ---------------------------------------------------------------------------

_EVENTS_QUERY_SOCKET = Path("/tmp/universal-protocol/events-query.sock")
_CONNECTION_EVENT = "federation.connection.established"


@dataclass(slots=True, kw_only=True)
class RelayConnectionResult:
    connected: bool
    reason: str | None = None


async def wait_for_relay_connected(
    remote_id: str,
    *,
    master_port: int = 9999,
    timeout: float = 90.0,
    interval: float = 3.0,
) -> RelayConnectionResult:
    """Wait for a relay to appear in master's model sources after deploy.

    Runs WebSocket event subscription (if event service is available) and
    HTTP poll concurrently; returns as soon as either confirms. The concurrent
    approach handles the race where the relay reconnects before we start
    subscribing.
    """
    tasks: list[asyncio.Task[bool]] = []
    if _EVENTS_QUERY_SOCKET.exists():
        tasks.append(asyncio.create_task(_subscribe_for_connection(remote_id, timeout)))
    tasks.append(
        asyncio.create_task(
            _poll_master_for_relay(remote_id, master_port, timeout, interval)
        )
    )

    connected = False
    pending: set[asyncio.Task[bool]] = set(tasks)
    while pending and not connected:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is None and task.result():
                connected = True
    for t in pending:
        t.cancel()
    if connected:
        return RelayConnectionResult(connected=True)
    return RelayConnectionResult(
        connected=False,
        reason=f"relay {remote_id} did not appear within {timeout}s",
    )


async def _subscribe_for_connection(remote_id: str, timeout: float) -> bool:
    """Subscribe to event service WebSocket for a matching connection event."""
    import aiohttp

    connector = aiohttp.UnixConnector(path=str(_EVENTS_QUERY_SOCKET))
    try:
        async with (
            aiohttp.ClientSession(connector=connector) as session,
            session.ws_connect(
                "http://localhost/v1/subscribe",
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as ws,
        ):
            await ws.send_json(
                {
                    "type": "subscribe",
                    "filter": {"signal": _CONNECTION_EVENT},
                }
            )

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    payload = data.get("payload", {})
                    if payload.get("remote_id") == remote_id:
                        return True
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
    except (TimeoutError, OSError, aiohttp.ClientError) as e:
        logger.debug("WebSocket subscription failed: %s", e)
    return False


async def _poll_master_for_relay(
    remote_id: str,
    master_port: int,
    timeout: float,
    interval: float,
) -> bool:
    """Fallback: poll master's model sources until remote_id appears."""
    from scripts.model_manager.topology import probe_federation_sources

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        sources = await asyncio.to_thread(probe_federation_sources, master_port)
        if sources is not None and remote_id in sources:
            return True
        await asyncio.sleep(interval)
    return False


async def restart_relay(
    *,
    hostname: str,
    address: str,
    build: bool = False,
) -> AsyncIterator[str]:
    """SSH into remote and restart (optionally rebuild) the relay.

    Skips rsync — assumes repo is already deployed. Use deploy_remote for
    first-time setup or when source changes need to be pushed.
    """
    node_env = _NODES_DIR / f"{hostname}.env"
    if not node_env.exists():
        yield f"[red]Node env not found: {node_env}. Run Deploy first.[/red]"
        return
    ssh_user = _read_node_env_key(node_env, "SSH_USER")
    if not ssh_user:
        yield f"[red]SSH_USER missing in {node_env}. Re-add the remote.[/red]"
        return
    ssh_target = f"{ssh_user}@{address}"
    relay_cmd = "./manage relay --restart"
    if build:
        relay_cmd += " --build"
    ssh_args = [
        "ssh",
        "-t",
        "-o",
        "BatchMode=yes",
        ssh_target,
        f"cd ~/universal-llm-gateway && {relay_cmd}",
    ]
    yield f"$ {' '.join(ssh_args)}"
    proc = await asyncio.create_subprocess_exec(
        *ssh_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc.stdout is None:
        yield "[red]ssh relay restart failed to stream output.[/red]"
        return
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip()
    code = await proc.wait()
    if code != 0:
        yield f"[red]ssh relay restart failed (exit {code}).[/red]"


def list_node_envs() -> list[Path]:
    """List all node env files in ~/.gateway/nodes/."""
    if not _NODES_DIR.exists():
        return []
    return sorted(_NODES_DIR.glob("*.env"))


def _read_node_env_key(node_env: Path, key: str) -> str | None:
    """Read a single KEY=value from a node env file, return value or None."""
    prefix = f"{key}="
    for line in node_env.read_text().splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def _write_node_env(
    *,
    hostname: str,
    model_path: str,
    ssh_user: str,
    edge_key: str,
    relay_key: str,
    master_host: str,
) -> Path:
    """Write ~/.gateway/nodes/<hostname>.env for a remote node."""
    _NODES_DIR.mkdir(parents=True, exist_ok=True)
    node_env = _NODES_DIR / f"{hostname}.env"

    lines = [
        f"NODE_ID={hostname}",
        f"MODEL_PATH={model_path}",
        f"SSH_USER={ssh_user}",
        f"FEDERATION_KEY_EDGE={edge_key}",
        f"RELAY_ID=relay-{hostname}",
        f"MASTER_HOST={master_host}",
        f"FEDERATION_KEY_RELAY={relay_key}",
    ]
    node_env.write_text("\n".join(lines) + "\n")
    logger.info("Generated node env: %s", node_env)
    return node_env


def _append_remote_to_config(
    *,
    hostname: str,
    address: str,
    relay_key: str,
) -> None:
    """Append a remote entry to ~/.gateway/stargate.yaml."""
    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    federation = data.get("federation", {})
    remotes: list[RemoteConfig] = list_remotes()

    relay_id = f"relay-{hostname}"
    for existing in remotes:
        if existing.get("stargate_id") == relay_id:
            raise ValueError(f"Remote '{relay_id}' already exists in config.")

    remotes.append(
        {
            "stargate_id": relay_id,
            "url": f"http://{address}:9999",
            "api_key": relay_key,
        }
    )

    federation["remotes"] = remotes
    data["federation"] = federation
    _MASTER_CONFIG.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False)
    )
    logger.info("Added remote %s → %s", relay_id, address)


def _get_local_address() -> str:
    """Best-effort local hostname for remote nodes to connect back to."""
    import socket

    try:
        return socket.gethostname()
    except OSError:
        return "localhost"
