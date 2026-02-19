"""Headless relay CLI: start/stop relay+edge on a remote host via ./manage relay."""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from scripts.model_manager.ui.controller.service_ctl import (
    ServiceController,
    load_env_file,
)

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
_NODES_DIR = Path.home() / ".gateway" / "nodes"
_RELAY_TEMPLATE = _ROOT / "config" / "templates" / "relay-stargate.yaml"
_COMPOSE_PATH = _ROOT / "docker" / "compose" / "gpu-edge.yml"
_STARGATE_SCRIPT = (
    _ROOT / "services" / "universal-stargate" / "scripts" / "start-stargate.sh"
)
_BUILD_SCRIPT = _ROOT / "docker" / "scripts" / "build" / "build-gpu.sh"


def _node_env_path(node_id: str) -> Path:
    return _NODES_DIR / f"{node_id}.env"


def _validate_node_env(env: dict[str, str]) -> list[str]:
    """Return list of missing required keys."""
    return [k for k in _REQUIRED_KEYS if not (env.get(k) or "").strip()]


def _build_env(node_env: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(load_env_file(_ROOT / ".env.local"))
    env.update(node_env)
    return env


def _run_build() -> int:
    if not _BUILD_SCRIPT.exists():
        print("ERROR: Build script not found:", _BUILD_SCRIPT, file=sys.stderr)
        return 1
    return subprocess.run(
        [str(_BUILD_SCRIPT), "--refresh-source"],
        cwd=str(_ROOT),
    ).returncode


def _run_stop(node_id: str) -> int:
    controller = ServiceController(_ROOT)
    import asyncio

    async def stop() -> str:
        out = await controller.stop_stargate()
        print(out)
        return out

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


def _run_start(node_id: str, node_env: dict[str, str], do_build: bool) -> int:
    if do_build and _run_build() != 0:
        return 1
    if not _COMPOSE_PATH.exists():
        print("ERROR: Compose file not found:", _COMPOSE_PATH, file=sys.stderr)
        return 1
    controller = ServiceController(_ROOT)
    model_path = Path(
        node_env.get("MODEL_PATH", str(Path.home() / ".models"))
    ).expanduser()
    err = controller.ensure_relay_dirs(node_id, model_path)
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
    stargate_env["STARGATE_CONFIG"] = str(_RELAY_TEMPLATE.resolve())
    return subprocess.run(
        [str(_STARGATE_SCRIPT), "debug"],
        env=stargate_env,
        cwd=str(_ROOT),
    ).returncode


def main() -> int:
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
        "--stop", action="store_true", help="Stop relay stargate and edge container"
    )
    args = parser.parse_args()
    node_id = (args.node_id or "").strip() or __import__("socket").gethostname()
    if args.restart:
        code = _run_stop(node_id)
        if code != 0:
            logger.warning("Stop returned %d, continuing with start", code)
        # fall through to _run_start below
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
    return _run_start(node_id, node_env, args.build)


if __name__ == "__main__":
    sys.exit(main())
