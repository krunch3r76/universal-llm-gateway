"""Headless relay CLI: start/stop relay+edge on a remote host via ./manage relay."""

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from scripts.model_manager.ui.controller.service_config import (
    ensure_relay_dirs,
    load_env_file,
)
from scripts.model_manager.ui.controller.service_ctl import ServiceController

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = (
    "NODE_ID",
    "MODEL_PATH",
    "FEDERATION_KEY_EDGE",
    "RELAY_ID",
    "MASTER_HOST",
    "FEDERATION_KEY_RELAY",
)
_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_RELAY_TEMPLATE = _ROOT / "config" / "templates" / "relay-stargate.yaml"
_COMPOSE_PATH = _ROOT / "docker" / "compose" / "gpu-edge.yml"
_STARGATE_SCRIPT = (
    _ROOT / "services" / "universal-stargate" / "scripts" / "start-stargate.sh"
)
_BUILD_SCRIPT = _ROOT / "docker" / "scripts" / "build" / "build-gpu.sh"

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _node_env_path(node_id: str) -> Path:
    return _NODES_DIR / f"{node_id}.env"


def _validate_node_env(env: dict[str, str]) -> list[str]:
    """Return list of missing required keys."""
    return [k for k in _REQUIRED_KEYS if not env.get(k, "").strip()]


def _build_env(node_env: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = dict(os.environ)
    env.update(load_env_file(_ROOT / ".env.local"))
    env.update(node_env)
    return env


def _render_template(template: Path, env: dict[str, str]) -> Path:
    """Expand ${VAR} placeholders in the relay template and write rendered config.

    Returns path to the rendered config file at ~/.gateway/stargate.yaml.
    """
    text = template.read_text()

    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        value = env.get(key)
        if value is None:
            error_msg = f"Template variable ${{{key}}} has no value in env. Cannot render template."
            logger.error(error_msg)
            raise ValueError(error_msg)
        return value

    rendered = _ENV_VAR_RE.sub(_replace, text)
    config_path = _GATEWAY_DIR / "relay-stargate.yaml"
    _GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
    config_path.write_text(rendered)
    logger.info("Rendered relay config: %s", config_path)
    return config_path


_SCOPE_FLAGS: dict[str, list[str]] = {
    "all": ["--cpu-native", "--gpu-native"],
    "llama": ["--cpu-native", "--gpu-native"],
}

_STARGATE_SENTINEL = "Stargate Proxy started successfully"
_STARGATE_STARTUP_TIMEOUT = 60


def _write_stargate_pid(pid: int) -> None:
    """Persist relay Stargate PID for ServiceController lifecycle operations."""
    pid_path = _GATEWAY_DIR / "stargate.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{pid}\n")


def _run_build(node_env: dict[str, str], scope: str = "all") -> int:
    if not _BUILD_SCRIPT.exists():
        print("ERROR: Build script not found:", _BUILD_SCRIPT, file=sys.stderr)
        return 1
    flags = _SCOPE_FLAGS.get(scope, _SCOPE_FLAGS["all"])
    return subprocess.run(
        [str(_BUILD_SCRIPT), *flags, "--refresh-source"],
        env=_build_env(node_env),
        cwd=str(_ROOT),
    ).returncode


def _run_stop(node_id: str) -> int:
    controller = ServiceController(_ROOT)
    import asyncio

    async def stop() -> str:
        try:
            out = await controller.stop_stargate()
            print(out)
            return out
        except Exception as e:
            logger.error("Failed to stop Stargate: %s", e)
            return f"Error stopping Stargate: {e}"

    asyncio.run(stop())
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_PATH),
            "-p",
            f"edge-{node_id}",
            "down",
        ],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and result.stderr:
        print(result.stderr, file=sys.stderr)
    return 0 if result.returncode == 0 else result.returncode


def _stop_existing_container(node_id: str) -> None:
    """Stop an existing edge container if running.

    Best-effort — failures are logged but not fatal. The subsequent
    ``docker compose up --force-recreate`` handles the restart regardless.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_PATH),
            "-p",
            f"edge-{node_id}",
            "down",
            "--timeout",
            "10",
        ],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info("Stopped existing edge-%s container", node_id)
    elif "no such service" not in (result.stderr or "").lower():
        logger.debug(
            "docker compose down returned %d (may be first deploy): %s",
            result.returncode,
            result.stderr.strip(),
        )


def _run_start(
    node_id: str, node_env: dict[str, str], do_build: bool, scope: str = "all"
) -> int:
    if do_build and _run_build(node_env, scope) != 0:
        return 1
    if not _COMPOSE_PATH.exists():
        print("ERROR: Compose file not found:", _COMPOSE_PATH, file=sys.stderr)
        return 1
    # Stop existing container to prevent bind-mount ownership races.
    # Docker daemon may have auto-restarted a previous container after reboot,
    # creating /tmp/universal-protocol as root via bind-mount auto-creation.
    _stop_existing_container(node_id)
    model_path = Path(
        node_env.get("MODEL_PATH", str(Path.home() / ".models"))
    ).expanduser()
    err = ensure_relay_dirs(_ROOT, node_id, model_path)
    if err:
        print("ERROR:", err, file=sys.stderr)
        return 1
    env = _build_env(node_env)
    env["COMPOSE_PROJECT_NAME"] = f"edge-{node_id}"
    result = subprocess.run(
        ["docker", "compose", "-f", str(_COMPOSE_PATH), "up", "-d", "--force-recreate"],
        env=env,
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Edge container failed:", result.stdout or result.stderr, file=sys.stderr)
        return 1
    if not _RELAY_TEMPLATE.exists():
        print("ERROR: Relay template not found:", _RELAY_TEMPLATE, file=sys.stderr)
        return 1
    if not _STARGATE_SCRIPT.exists():
        print("ERROR: start-stargate.sh not found:", _STARGATE_SCRIPT, file=sys.stderr)
        return 1
    stargate_env = _build_env(node_env)
    rendered_config = _render_template(_RELAY_TEMPLATE, stargate_env)
    stargate_env["STARGATE_CONFIG"] = str(rendered_config)
    return _launch_stargate(node_id, stargate_env)


def _launch_stargate(node_id: str, stargate_env: dict[str, str]) -> int:
    """Start Stargate detached, tail its log until startup is confirmed.

    Uses start_new_session=True so the Stargate survives SSH disconnection.
    Monitors the log file for the startup sentinel or early exit.
    """
    log_path = Path(f"/tmp/logs/relay/stargate-{node_id}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w") as log_fh:
        proc = subprocess.Popen(
            [str(_STARGATE_SCRIPT), "debug"],
            env=stargate_env,
            cwd=str(_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.monotonic() + _STARGATE_STARTUP_TIMEOUT
    with log_path.open("r") as reader:
        while time.monotonic() < deadline:
            line = reader.readline()
            if line:
                print(line.rstrip())
                if _STARGATE_SENTINEL in line:
                    _write_stargate_pid(proc.pid)
                    print(f"Relay started (PID {proc.pid})")
                    return 0
            elif proc.poll() is not None:
                remaining_output = "".join(reader).strip()
                if remaining_output:
                    logger.error(
                        "Stargate exited early (code %d). Output:\n%s",
                        proc.returncode,
                        remaining_output,
                    )
                else:
                    logger.error("Stargate exited early (code %d)", proc.returncode)
                (_GATEWAY_DIR / "stargate.pid").unlink(missing_ok=True)
                return proc.returncode
            else:
                time.sleep(0.2)

    if proc.poll() is None:
        _write_stargate_pid(proc.pid)
        logger.warning(
            "Stargate running (PID %d) but startup not confirmed within %ds",
            proc.pid,
            _STARGATE_STARTUP_TIMEOUT,
        )
        return 0
    return proc.returncode


def main() -> int:
    """Parse CLI args and dispatch relay stop/restart/start operations."""
    parser = argparse.ArgumentParser(
        description="Start or stop relay+edge on this host (headless; no TUI)."
    )
    parser.add_argument(
        "--node-id",
        metavar="ID",
        help="Override hostname for node env lookup (default: hostname)",
    )
    parser.add_argument(
        "--build", action="store_true", help="Build Docker image before starting"
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Stop running relay+edge, then start again (combine with --build to rebuild)",
    )
    parser.add_argument(
        "--scope",
        choices=["all", "llama"],
        default="all",
        help="Build scope: 'all' (vLLM + llama) or 'llama' only (default: all)",
    )
    parser.add_argument(
        "--stop", action="store_true", help="Stop relay stargate and edge container"
    )
    args = parser.parse_args()
    node_id = (args.node_id or "").strip() or __import__("socket").gethostname()
    if args.restart:
        code = _run_stop(node_id)
        if code != 0:
            logger.warning("Stop returned %d, continuing with start", code)
    elif args.stop:
        return _run_stop(node_id)
    node_path = _node_env_path(node_id)
    if not node_path.exists():
        print(
            "ERROR: Node env not found:",
            node_path,
            "\nRun 'Add Remote' on master, then scp the node env to this host.",
            file=sys.stderr,
        )
        return 1
    node_env = load_env_file(node_path)
    missing = _validate_node_env(node_env)
    if missing:
        print(
            "ERROR: Node env missing required keys:",
            ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    return _run_start(node_id, node_env, args.build, scope=args.scope)


if __name__ == "__main__":
    sys.exit(main())
