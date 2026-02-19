"""Topology management — add/remove remote nodes in Master Stargate config."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_MASTER_CONFIG = _GATEWAY_DIR / "stargate.yaml"


def list_remotes() -> list[dict[str, Any]]:
    """Read remotes from ~/.gateway/stargate.yaml federation section."""
    if not _MASTER_CONFIG.exists():
        return []
    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    federation = data.get("federation", {})
    return federation.get("remotes") or []


def add_remote(
    *,
    hostname: str,
    address: str,
    model_path: str,
) -> dict[str, str]:
    """Add a remote node to the Master Stargate config and generate node env.

    Generates federation keys, writes ~/.gateway/nodes/<hostname>.env,
    and appends a remote entry to ~/.gateway/stargate.yaml.

    Args:
        hostname: Node identifier (e.g., "jupiter")
        address: Network address (e.g., "jupiter", "192.168.1.50")
        model_path: Host path to models on the remote machine

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
) -> AsyncIterator[str]:
    """Deploy relay+edge to a remote host via rsync + ssh.

    Yields log lines as they appear. Three steps:
    1. rsync repo (excludes local-only files)
    2. scp ~/.gateway/nodes/<hostname>.env
    3. ssh ./manage relay [--restart] [--build]
    """
    repo = workspace_root.resolve()
    dest = f"{address}:~/universal-llm-gateway/"
    node_env = _NODES_DIR / f"{hostname}.env"
    if not node_env.exists():
        yield f"[red]Node env not found: {node_env}. Add Remote first.[/red]"
        return

    rsync_excludes = [
        ".env.local",
        "tmp/gpu-nodes",
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "*.pyc",
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
    assert proc.stdout is not None
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip()
    code = await proc.wait()
    if code != 0:
        yield f"[red]rsync failed (exit {code}).[/red]"
        return

    scp_args = [
        "scp",
        str(node_env),
        f"{address}:~/.gateway/nodes/{hostname}.env",
    ]
    yield f"$ {' '.join(scp_args)}"
    proc = await asyncio.create_subprocess_exec(
        *scp_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
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
        relay_cmd += " --build"
    ssh_args = [
        "ssh",
        "-t",
        "-o",
        "BatchMode=yes",
        address,
        f"cd ~/universal-llm-gateway && {relay_cmd}",
    ]
    yield f"$ ssh -t -o BatchMode=yes {address} 'cd ~/universal-llm-gateway && {relay_cmd}'"
    proc = await asyncio.create_subprocess_exec(
        *ssh_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip()
    code = await proc.wait()
    if code != 0:
        yield f"[red]ssh relay failed (exit {code}).[/red]"
        return


def list_node_envs() -> list[Path]:
    """List all node env files in ~/.gateway/nodes/."""
    if not _NODES_DIR.exists():
        return []
    return sorted(_NODES_DIR.glob("*.env"))


def _write_node_env(
    *,
    hostname: str,
    model_path: str,
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
    remotes: list[dict[str, Any]] = federation.get("remotes") or []

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
