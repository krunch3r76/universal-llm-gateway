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
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from scripts.model_manager.ui.controller.service_config import (
    build_service_env,
    ensure_bind_mount_dirs,
    ensure_node_env,
    ensure_socket_dir,
    load_env_file,
)
from scripts.model_manager.update_targets import collect_remote_targets

logger = logging.getLogger(__name__)

# Process registry for Ctrl+C cancellation when running --all-nodes concurrently
_active_processes: set[subprocess.Popen] = set()
_active_lock = threading.Lock()
_cancel_event = threading.Event()

_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILD_SCRIPT = _ROOT / "docker" / "scripts" / "build" / "build-gpu.sh"
_COMPOSE_PATH = _ROOT / "docker" / "compose" / "gpu-edge.yml"

_GITHUB_REPOS: dict[str, str] = {
    "llama-server": "ggml-org/llama.cpp",
    "vllm": "vllm-project/vllm",
}

_BUILD_FLAGS: dict[str, str] = {
    "llama-server": "--llama-server-version",
    "vllm": "--vllm-version",
}

_GITHUB_API_TIMEOUT = 10


def _sigint_handler(_signum: int, _frame: object) -> None:
    """On Ctrl+C, terminate all active subprocesses so they can be cancelled."""
    _cancel_event.set()
    with _active_lock:
        for proc in _active_processes:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


def _run_with_registry(
    args: list[str],
    *,
    cwd: str | Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> int:
    """Run a subprocess, registering it for cancellation. Returns exit code."""
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdout=None if not capture else subprocess.PIPE,
        stderr=None if not capture else subprocess.STDOUT,
        text=capture,
        start_new_session=True,
    )
    with _active_lock:
        _active_processes.add(proc)
    try:
        if capture:
            out, _ = proc.communicate()
            if out:
                print(out, end="")
        else:
            proc.wait()
    finally:
        with _active_lock:
            _active_processes.discard(proc)
    return proc.returncode if proc.returncode is not None else -1


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

    args = [
        str(_BUILD_SCRIPT),
        "--cpu-native",
        "--gpu-native",
        "--no-cache",
    ]
    for component, tag in versions.items():
        args.append(f"{_BUILD_FLAGS[component]}={tag}")

    label = ", ".join(f"{c} {v}" for c, v in versions.items())
    print(f"\nBuilding ({label})...")
    print(f"$ {' '.join(args)}")

    code = _run_with_registry(args, cwd=_ROOT)
    if code != 0:
        print(f"ERROR: Build failed (exit {code})", file=sys.stderr)
    return code


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
    code = _run_with_registry(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_PATH),
            "up",
            "-d",
            "--force-recreate",
        ],
        cwd=_ROOT,
        env=env,
        capture=True,
    )
    if code == 0:
        print(f"  edge-{node_id} recreated.")
    else:
        print(f"ERROR: Failed to restart edge-{node_id} (exit {code})")
    return code


def run_remote(
    hostname: str,
    ssh_user: str,
    versions: dict[str, str],
) -> int:
    """SSH into a remote node and run build + restart."""
    version_flags = " ".join(f"{_BUILD_FLAGS[c]}={v}" for c, v in versions.items())
    build_cmd = (
        f"./docker/scripts/build/build-gpu.sh --cpu-native --gpu-native --no-cache "
        f"{version_flags}"
    )
    restart_cmd = "./manage relay --restart"
    remote_cmd = f"cd ~/universal-llm-gateway && {build_cmd} && {restart_cmd}"

    ssh_target = f"{ssh_user}@{hostname}"
    ssh_args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        ssh_target,
        remote_cmd,
    ]

    print(f"\n--- Remote: {hostname} ---")
    print(f"$ {' '.join(ssh_args)}")

    code = _run_with_registry(ssh_args, cwd=_ROOT)
    if code != 0:
        print(
            f"ERROR: Remote update on {hostname} failed (exit {code})",
            file=sys.stderr,
        )
    return code


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


def _run_local_task(versions: dict[str, str], build_only: bool) -> int:
    """Build + restart locally. Returns exit code."""
    code = run_build(versions)
    if code != 0:
        return code
    if build_only:
        return 0
    return restart_local_edge()


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
    _cancel_event.clear()
    versions = _resolve_versions(args.components, args.version)

    run_remotes = bool(args.all_nodes or args.remote)
    remote_targets = collect_remote_targets(args.remote) if run_remotes else []

    if run_remotes and args.remote and not remote_targets:
        return 1

    # Concurrent execution: local + all remotes in parallel (cancellable via Ctrl+C)
    if run_remotes and remote_targets:
        signal.signal(signal.SIGINT, _sigint_handler)
        try:
            with ThreadPoolExecutor(max_workers=1 + len(remote_targets)) as ex:
                futures = {
                    ex.submit(_run_local_task, versions, args.build_only): "local",
                }
                for hostname, ssh_user in remote_targets:
                    fut = ex.submit(run_remote, hostname, ssh_user, versions)
                    futures[fut] = hostname

                worst = 0
                for fut in as_completed(futures):
                    if _cancel_event.is_set():
                        break
                    try:
                        code = fut.result()
                        worst = max(worst, code)
                    except Exception as e:
                        name = futures.get(fut, "?")
                        print(f"ERROR: {name} failed: {e}", file=sys.stderr)
                        worst = max(worst, 1)

            if _cancel_event.is_set():
                print("\nCancelled.", file=sys.stderr)
                return 130
            if worst != 0:
                return worst
        finally:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
    else:
        # Local only
        code = _run_local_task(versions, args.build_only)
        if code != 0:
            return code

    if args.prune:
        prune_images()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
