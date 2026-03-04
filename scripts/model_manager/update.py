"""Headless CLI: update llama-server and/or vLLM to latest release.

Fetches the latest GitHub release tag, rebuilds the relevant Docker stage
(per-stage cache isolation keeps the other stage untouched), restarts the
local edge container, and optionally repeats on remote nodes via SSH.

Usage:
    ./manage update llama-server
    ./manage update vllm
    ./manage update llama-server vllm
    ./manage update llama-server --all-nodes
    ./manage update llama-server --version=b5678
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import yaml

from scripts.model_manager.ui.controller.service_config import (
    build_service_env,
    ensure_bind_mount_dirs,
    ensure_node_env,
    ensure_socket_dir,
    load_env_file,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILD_SCRIPT = _ROOT / "docker" / "scripts" / "build" / "build-gpu.sh"
_COMPOSE_PATH = _ROOT / "docker" / "compose" / "gpu-edge.yml"
_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_MASTER_CONFIG = _GATEWAY_DIR / "stargate.yaml"

_GITHUB_REPOS: dict[str, str] = {
    "llama-server": "ggml-org/llama.cpp",
    "vllm": "vllm-project/vllm",
}

_BUILD_FLAGS: dict[str, str] = {
    "llama-server": "--llama-server-version",
    "vllm": "--vllm-version",
}

_GITHUB_API_TIMEOUT = 10


def fetch_latest_release(repo: str) -> str | None:
    """Fetch the latest release tag from a GitHub repo. Returns None on failure."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        with urlopen(url, timeout=_GITHUB_API_TIMEOUT) as resp:
            data = json.loads(resp.read())
            tag = data.get("tag_name")
            if not tag:
                print(f"ERROR: No tag_name in response from {url}", file=sys.stderr)
                return None
            return tag
    except (URLError, json.JSONDecodeError, KeyError) as e:
        print(f"ERROR: Failed to fetch latest release from {url}: {e}", file=sys.stderr)
        return None


def _resolve_versions(
    components: list[str], pinned_version: str | None
) -> dict[str, str]:
    """Resolve version tags for each component.

    Returns dict mapping component -> version tag, or exits on failure.
    """
    versions: dict[str, str] = {}
    for component in components:
        if pinned_version:
            versions[component] = pinned_version
            continue
        repo = _GITHUB_REPOS[component]
        print(f"Fetching latest {component} release...", end=" ", flush=True)
        tag = fetch_latest_release(repo)
        if not tag:
            sys.exit(1)
        print(tag)
        versions[component] = tag
    return versions


def run_build(versions: dict[str, str]) -> int:
    """Run build-gpu.sh with version flags for each component."""
    if not _BUILD_SCRIPT.exists():
        print(f"ERROR: Build script not found: {_BUILD_SCRIPT}", file=sys.stderr)
        return 1

    args = [str(_BUILD_SCRIPT), "--cpu-native", "--gpu-native"]
    for component, tag in versions.items():
        args.append(f"{_BUILD_FLAGS[component]}={tag}")

    label = ", ".join(f"{c} {v}" for c, v in versions.items())
    print(f"\nBuilding ({label})...")
    print(f"$ {' '.join(args)}")

    result = subprocess.run(args, cwd=str(_ROOT))
    if result.returncode != 0:
        print(f"ERROR: Build failed (exit {result.returncode})", file=sys.stderr)
    return result.returncode


def restart_local_edge(node_id: str = "localhost") -> int:
    """Restart the local edge container with the newly built image."""
    if not _COMPOSE_PATH.exists():
        print(f"ERROR: Compose file not found: {_COMPOSE_PATH}", file=sys.stderr)
        return 1

    socket_error = ensure_socket_dir()
    if socket_error:
        print(f"ERROR: {socket_error}", file=sys.stderr)
        return 1

    node_env_path = ensure_node_env(_ROOT, node_id)
    node_env = load_env_file(node_env_path)
    model_path = Path(node_env.get("MODEL_PATH", str(Path.home() / ".models")))
    ownership_error = ensure_bind_mount_dirs(_ROOT, node_id, model_path)
    if ownership_error:
        print(f"ERROR: {ownership_error}", file=sys.stderr)
        return 1

    env = build_service_env(_ROOT, node_env_path)
    env["COMPOSE_PROJECT_NAME"] = f"edge-{node_id}"

    print("\nRestarting edge container...")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_PATH),
            "up",
            "-d",
            "--force-recreate",
        ],
        env=env,
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        print(f"  edge-{node_id} recreated.")
    else:
        print(f"ERROR: Failed to restart edge-{node_id} (exit {result.returncode})")
        if output.strip():
            print(output.strip())
    return result.returncode


def _list_remotes() -> list[dict[str, str]]:
    """Read remote nodes from ~/.gateway/stargate.yaml."""
    if not _MASTER_CONFIG.exists():
        return []
    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    return data.get("federation", {}).get("remotes") or []


def _read_node_env_key(node_env: Path, key: str) -> str | None:
    """Read a single KEY=value from a node env file."""
    prefix = f"{key}="
    for line in node_env.read_text().splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def _hostname_from_remote(remote: dict[str, str]) -> str:
    """Extract hostname from remote URL (e.g. 'http://jupiter:9999' -> 'jupiter')."""
    url = remote.get("url", "")
    parsed = urlparse(url)
    return parsed.hostname or ""


def run_remote(
    hostname: str,
    ssh_user: str,
    versions: dict[str, str],
) -> int:
    """SSH into a remote node and run build + restart."""
    version_flags = " ".join(f"{_BUILD_FLAGS[c]}={v}" for c, v in versions.items())
    build_cmd = (
        f"./docker/scripts/build/build-gpu.sh --cpu-native --gpu-native {version_flags}"
    )
    restart_cmd = "./manage relay --restart"
    remote_cmd = f"cd ~/universal-llm-gateway && {build_cmd} && {restart_cmd}"

    ssh_target = f"{ssh_user}@{hostname}"
    ssh_args = [
        "ssh",
        "-t",
        "-o",
        "BatchMode=yes",
        ssh_target,
        remote_cmd,
    ]

    print(f"\n--- Remote: {hostname} ---")
    print(f"$ {' '.join(ssh_args)}")

    result = subprocess.run(ssh_args)
    if result.returncode != 0:
        print(
            f"ERROR: Remote update on {hostname} failed (exit {result.returncode})",
            file=sys.stderr,
        )
    return result.returncode


def prune_images() -> int:
    """Remove dangling Docker images to reclaim disk space."""
    print("\nPruning dangling images...")
    result = subprocess.run(
        ["docker", "image", "prune", "-f"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout:
        print(f"  {result.stdout.strip()}")
    return result.returncode


def _run_on_remotes(
    versions: dict[str, str],
    *,
    target_host: str | None = None,
) -> int:
    """Build + restart on remote nodes. Returns worst exit code."""
    remotes = _list_remotes()
    if not remotes:
        print("No remote nodes configured.", file=sys.stderr)
        return 1

    worst = 0
    for remote in remotes:
        hostname = _hostname_from_remote(remote)
        if not hostname:
            continue
        if target_host and hostname != target_host:
            continue

        node_env = _NODES_DIR / f"{hostname}.env"
        if not node_env.exists():
            print(f"WARNING: Node env not found: {node_env}, skipping {hostname}")
            worst = max(worst, 1)
            continue

        ssh_user = _read_node_env_key(node_env, "SSH_USER")
        if not ssh_user:
            print(f"WARNING: SSH_USER missing in {node_env}, skipping {hostname}")
            worst = max(worst, 1)
            continue

        code = run_remote(hostname, ssh_user, versions)
        worst = max(worst, code)

    if (
        target_host
        and worst == 0
        and not any(_hostname_from_remote(r) == target_host for r in remotes)
    ):
        print(f"ERROR: Remote '{target_host}' not found in federation config.")
        return 1

    return worst


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update llama-server and/or vLLM to the latest release.",
    )
    parser.add_argument(
        "components",
        nargs="+",
        choices=["llama-server", "vllm"],
        help="Component(s) to update",
    )
    parser.add_argument(
        "--version",
        metavar="TAG",
        help="Pin to a specific release tag instead of fetching latest",
    )
    parser.add_argument(
        "--all-nodes",
        action="store_true",
        help="Also build and restart on all remote nodes",
    )
    parser.add_argument(
        "--remote",
        metavar="HOST",
        help="Build and restart on a specific remote node",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build without restarting the edge container",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Prune dangling Docker images after restart",
    )
    args = parser.parse_args()

    versions = _resolve_versions(args.components, args.version)

    code = run_build(versions)
    if code != 0:
        return code

    if not args.build_only:
        code = restart_local_edge()
        if code != 0:
            return code

    if args.all_nodes or args.remote:
        remote_code = _run_on_remotes(
            versions,
            target_host=args.remote,
        )
        if remote_code != 0:
            return remote_code

    if args.prune:
        prune_images()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
