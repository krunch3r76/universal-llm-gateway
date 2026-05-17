"""Topology management for federated remotes.

This module owns remote CRUD in ``~/.gateway/stargate.yaml``, emits node env files
for deployment, and provides async deploy/restart flows used by the TUI.
"""

import asyncio
import json
import logging
import os
import secrets
import shlex
import signal
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import yaml

from scripts.model_manager.observation_event import (
    emit_build_image_completed,
    emit_build_image_mismatch,
    emit_build_image_started,
)

logger = logging.getLogger(__name__)

_GATEWAY_GPU_IMAGE = "universal-llm-gateway:gpu"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
# Compared between master and relay images (excludes build.timestamp — always differs).
_BUILD_MISMATCH_LABEL_KEYS: tuple[str, ...] = (
    "build.vllm.version",
    "build.vllm.from.source",
    "build.torch.nightly.date",
    "build.llama.server.version",
    "build.llama.cpp.python.version",
    "build.llama.cpp.python.enabled",
    "cpu.optimization",
    "gpu.arch",
    "gpu.cuda_version",
    "vllm.enabled",
)

_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_MASTER_CONFIG = _GATEWAY_DIR / "stargate.yaml"
_FORWARDED_BUILD_ENV_KEYS = (
    "ENABLE_VLLM",
    "VLLM_FROM_SOURCE",
    "VLLM_BUILD_ARGS",
    "VLLM_EXTRA_FLAGS",
    "VLLM_MAX_JOBS",
    "VLLM_VERSION",
    "TORCH_NIGHTLY_DATE",
    "ENABLE_LLAMA_CPP_PYTHON",
    "LLAMA_CPP_PYTHON_VERSION",
    "ENABLE_LLAMA_SERVER",
    "LLAMA_SERVER_VERSION",
)


class RemoteConfig(TypedDict):
    stargate_id: str
    url: str
    api_key: str


def _string_field(remote: dict[str, Any], key: str) -> str:
    """Read a remote config field as string, defaulting to empty string."""
    value = remote.get(key, "")
    return value if isinstance(value, str) else str(value)


def _relay_remote_command(relay_cmd: str) -> str:
    """Prefix remote relay commands with forwarded build env vars, if set."""
    forwarded = [
        f"{key}={shlex.quote(value)}"
        for key in _FORWARDED_BUILD_ENV_KEYS
        if (value := os.environ.get(key))
    ]
    # PYTHONPATH is set after cd so $(pwd) expands to the remote project root.
    # This overrides the system sitecustomize.py that would otherwise shadow the
    # venv's site-packages copy and leave libs/ off sys.path.
    forwarded = [
        "PYTHONPATH=$(pwd)/libs:$(pwd)/services/universal-stargate${PYTHONPATH:+:$PYTHONPATH}",
        *forwarded,
    ]
    prefix = " ".join(forwarded)
    command_prefix = f"{prefix} " if prefix else ""
    command = f"cd ~/universal-llm-gateway && {command_prefix}{relay_cmd}"
    wrapped = (
        # On remote start/restart flows, successful shell exit must not tear down
        # the detached relay services we just launched.
        "trap 'pkill -TERM -g $$ 2>/dev/null; wait 2>/dev/null' HUP INT TERM; "
        f"{command}"
    )
    return f"bash -lc {shlex.quote(wrapped)}"


async def _terminate_process_group(
    proc: asyncio.subprocess.Process, *, sigkill_timeout: float = 3.0
) -> None:
    """Terminate a subprocess group spawned with ``start_new_session=True``."""
    if proc.returncode is not None:
        return
    try:
        _ = os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        logger.debug("Process group already exited (pid=%s)", proc.pid)
        return
    except PermissionError as e:
        logger.warning("Permission denied sending SIGTERM to pid %s: %s", proc.pid, e)
        try:
            proc.terminate()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(proc.wait(), timeout=sigkill_timeout)
        return
    except TimeoutError:
        pass
    try:
        _ = os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as e:
        logger.warning("Permission denied sending SIGKILL to pid %s: %s", proc.pid, e)
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except TimeoutError:
        logger.warning("Subprocess %s did not exit after SIGKILL", proc.pid)


class _StreamedCommandError(Exception):
    """Internal control-flow marker for streamed subprocess failures."""


async def _stream_subprocess(
    args: list[str],
    *,
    stream_failure_line: str,
    exit_failure_prefix: str,
    cwd: str | None = None,
) -> AsyncIterator[str]:
    """Run a subprocess and yield decoded stdout lines as they arrive."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        if proc.stdout is None:
            yield stream_failure_line
            await _terminate_process_group(proc)
            raise _StreamedCommandError
        async for raw in proc.stdout:
            yield raw.decode(errors="replace").rstrip()
        code = await proc.wait()
    except asyncio.CancelledError:
        await _terminate_process_group(proc)
        raise
    if code != 0:
        yield f"{exit_failure_prefix} (exit {code}).[/red]"
        raise _StreamedCommandError


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
        remote_dict: dict[str, Any] = {str(k): v for k, v in remote.items()}
        remotes.append(
            {
                "stargate_id": _string_field(remote_dict, "stargate_id"),
                "url": _string_field(remote_dict, "url"),
                "api_key": _string_field(remote_dict, "api_key"),
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


async def _docker_inspect_labels_local(image: str) -> dict[str, str] | None:
    """Return Config.Labels for a local image, or None if missing / inspect fails."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "image",
        "inspect",
        image,
        "--format",
        "{{json .Config.Labels}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    raw = await proc.stdout.read()
    code = await proc.wait()
    if code != 0:
        return None
    try:
        data = json.loads(raw.decode(errors="replace").strip() or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in data.items()}


async def _docker_inspect_labels_remote(
    ssh_target: str, image: str
) -> dict[str, str] | None:
    """Return Config.Labels for image on remote host via SSH."""
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o",
        "BatchMode=yes",
        ssh_target,
        "docker",
        "image",
        "inspect",
        image,
        "--format",
        "{{json .Config.Labels}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    raw = await proc.stdout.read()
    code = await proc.wait()
    if code != 0:
        return None
    try:
        data = json.loads(raw.decode(errors="replace").strip() or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in data.items()}


def _pick_build_labels(labels: dict[str, str]) -> dict[str, str]:
    return {k: labels.get(k, "") for k in _BUILD_MISMATCH_LABEL_KEYS}


def _diff_build_labels(
    local_l: dict[str, str], remote_l: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Return (human-readable diffs, keys that differ)."""
    diffs: list[str] = []
    keys_out: list[str] = []
    for key in _BUILD_MISMATCH_LABEL_KEYS:
        lv = local_l.get(key, "")
        rv = remote_l.get(key, "")
        if lv != rv:
            keys_out.append(key)
            diffs.append(f"{key}: local={lv!r} remote={rv!r}")
    return diffs, keys_out


async def gateway_image_mismatch_warnings(
    *,
    hostname: str,
    address: str,
) -> list[str]:
    """Compare gateway GPU image build labels local vs remote; warn on drift.

    Fails open: missing image or inspect errors yield a single advisory line, not
    an exception.
    """
    node_env = _NODES_DIR / f"{hostname}.env"
    ssh_user = _read_node_env_key(node_env, "SSH_USER") if node_env.exists() else None
    if not ssh_user:
        return []

    local_labels = await _docker_inspect_labels_local(_GATEWAY_GPU_IMAGE)
    if local_labels is None:
        return [
            f"[{hostname}] Image check skipped: local {_GATEWAY_GPU_IMAGE} not found "
            "(build or pull on master first)."
        ]

    ssh_target = f"{ssh_user}@{address}"
    remote_labels = await _docker_inspect_labels_remote(ssh_target, _GATEWAY_GPU_IMAGE)
    if remote_labels is None:
        return [
            f"[{hostname}] Image check skipped: remote {_GATEWAY_GPU_IMAGE} not found "
            "or docker inspect failed."
        ]

    local_pick = _pick_build_labels(local_labels)
    remote_pick = _pick_build_labels(remote_labels)
    diffs, keys = _diff_build_labels(local_pick, remote_pick)
    if not diffs:
        return []

    await emit_build_image_mismatch(
        host=hostname,
        mismatched_fields=diffs,
        local_labels=local_pick,
        remote_labels=remote_pick,
    )
    msg = (
        f"[{hostname}] WARNING: gateway image build metadata differs from master — "
        + "; ".join(diffs)
        + ". Run Rebuild + Deploy All on master and remotes (or rebuild locally) to align."
    )
    return [msg]


async def stop_remote(*, hostname: str, address: str) -> AsyncIterator[str]:
    """Stop relay Stargate and edge container on a remote host."""
    node_env = _NODES_DIR / f"{hostname}.env"
    if not node_env.exists():
        yield f"[red]Node env not found: {node_env}. Add Remote first.[/red]"
        return

    ssh_user = _read_node_env_key(node_env, "SSH_USER")
    if not ssh_user:
        yield f"[red]SSH_USER missing in {node_env}. Re-add the remote.[/red]"
        return

    ssh_target = f"{ssh_user}@{address}"
    remote_cmd = _relay_remote_command("./manage relay --stop")
    ssh_args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        ssh_target,
        remote_cmd,
    ]
    yield f"$ {shlex.join(ssh_args)}"
    try:
        async for line in _stream_subprocess(
            ssh_args,
            stream_failure_line="[red]ssh relay stop failed to stream output.[/red]",
            exit_failure_prefix="[red]ssh relay stop failed",
        ):
            yield line
    except _StreamedCommandError:
        return


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
    1. rsync repo (.gitignore rules + explicit .git exclude)
    2. scp ~/.gateway/nodes/<hostname>.env
    3. ssh ./manage relay [--restart] [--build] [--scope SCOPE]

    Edge compose bind-mounts ``libs/`` and ``services/`` from the rsynced tree, so a
    restart without *build* picks up Python changes without rebuilding the image.
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

    # Sync working tree (staged or not); exclude paths per .gitignore. .git is
    # not listed in .gitignore — exclude explicitly.
    rsync_args = [
        "rsync",
        "-az",
        "--delete",
        "--exclude=.git",
        "--filter=:- .gitignore",
        f"{repo}/",
        dest,
    ]
    yield f"$ {shlex.join(rsync_args)}"
    try:
        async for line in _stream_subprocess(
            rsync_args,
            cwd=str(repo),
            stream_failure_line="[red]rsync failed to stream output.[/red]",
            exit_failure_prefix="[red]rsync failed",
        ):
            yield line
    except _StreamedCommandError:
        return

    scp_args = [
        "scp",
        str(node_env),
        f"{ssh_target}:~/.gateway/nodes/{hostname}.env",
    ]
    yield f"$ {shlex.join(scp_args)}"
    try:
        async for line in _stream_subprocess(
            scp_args,
            stream_failure_line="[red]scp failed to stream output.[/red]",
            exit_failure_prefix="[red]scp failed",
        ):
            yield line
    except _StreamedCommandError:
        return

    relay_cmd = "./manage relay"
    if restart:
        relay_cmd += " --restart"
    if build:
        relay_cmd += f" --build --scope {scope}"
    remote_cmd = _relay_remote_command(relay_cmd)
    ssh_args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        ssh_target,
        remote_cmd,
    ]
    yield f"$ {shlex.join(ssh_args)}"
    build_t0 = time.monotonic() if build else 0.0
    if build:
        await emit_build_image_started(host=hostname, scope=scope)
    try:
        async for line in _stream_subprocess(
            ssh_args,
            stream_failure_line="[red]ssh relay failed to stream output.[/red]",
            exit_failure_prefix="[red]ssh relay failed",
        ):
            yield line
    except _StreamedCommandError:
        if build:
            await emit_build_image_completed(
                host=hostname,
                scope=scope,
                success=False,
                duration_s=time.monotonic() - build_t0,
            )
        return
    if build:
        await emit_build_image_completed(
            host=hostname,
            scope=scope,
            success=True,
            duration_s=time.monotonic() - build_t0,
        )


# ---------------------------------------------------------------------------
# Event-driven relay readiness
# ---------------------------------------------------------------------------

_EVENTS_QUERY_SOCKET = Path(
    os.environ.get("EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock")
)
_CONNECTION_EVENT = "federation.connection.established"


@dataclass(slots=True, kw_only=True)
class RelayConnectionResult:
    """Result of relay-connection wait with optional non-success reason."""

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
        async with asyncio.timeout(timeout):
            async with (
                aiohttp.ClientSession(connector=connector) as session,
                session.ws_connect("http://localhost/v1/subscribe") as ws,
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
                            logger.warning(
                                "Ignoring malformed event-service message while waiting for %s",
                                remote_id,
                            )
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
        logger.exception("WebSocket subscription failed: %s", e)
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
    """Sync the repo to a remote and then restart (optionally rebuild) the relay."""
    async for line in deploy_remote(
        hostname=hostname,
        address=address,
        workspace_root=_WORKSPACE_ROOT,
        build=build,
        restart=True,
    ):
        yield line


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
    if not _MASTER_CONFIG.exists():
        raise FileNotFoundError(
            f"Master config not found: {_MASTER_CONFIG}. "
            "Start local Stargate before adding remotes."
        )
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
