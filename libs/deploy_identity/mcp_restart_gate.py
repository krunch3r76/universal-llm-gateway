"""MCP restart gate — import probe, snapshot, pending/booted, and restore decisions.

The host script ``scripts/sync-and-restart-mcp.sh`` and the manage edge-probe
backup share this module. Docker argv plans are exposed for hermetic tests.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROBE_WALL_S = 120
HEALTH_WAIT_S = 120
CONTAINER_NAME = "mcp-server"
LOCAL_IMAGE = "universal-mcp-server:local"
LAST_HEALTHY_TAG = "universal-mcp-server:last-healthy"

COLD_IMPORTS = (
    "server",
    "tools.file_editor",
    "tools.filesystem",
    "tools.filesystem._files_dispatcher",
)

SNAPSHOT_ROOTS = ("libs", "services", "config", "pipelines")
RESTORE_ELIGIBLE = frozenset(
    {"unhealthy", "exited", "dead", "created", "none", "starting_elapsed"}
)


def source_tree_sha(workspace_root: Path) -> str:
    """Hash of the MCP COPY set on disk (matches sync script semantics)."""
    paths: list[Path] = []
    for root_name in ("libs", "services/mcp-server"):
        root = workspace_root / root_name.replace("/", os.sep)
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(
                ".pyc"
            ):
                paths.append(p)
    paths.sort(key=lambda p: str(p))
    h = hashlib.sha256()
    for p in paths:
        h.update(str(p.relative_to(workspace_root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def last_healthy_dir(mcp_data_dir: Path) -> Path:
    return mcp_data_dir / "last-healthy"


def last_healthy_lock(mcp_data_dir: Path) -> Path:
    return mcp_data_dir / "last-healthy.lock"


def _read_sha_file(base: Path, name: str) -> str | None:
    path = base / name
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_pending(mcp_data_dir: Path) -> str | None:
    return _read_sha_file(last_healthy_dir(mcp_data_dir), "PENDING")


def read_booted(mcp_data_dir: Path) -> str | None:
    return _read_sha_file(last_healthy_dir(mcp_data_dir), "BOOTED")


def write_pending(mcp_data_dir: Path, sha: str) -> None:
    base = last_healthy_dir(mcp_data_dir)
    base.mkdir(parents=True, exist_ok=True)
    (base / "PENDING").write_text(f"{sha}\n", encoding="utf-8")


def promote_pending_to_booted(mcp_data_dir: Path) -> None:
    pending = read_pending(mcp_data_dir)
    if pending is None:
        return
    base = last_healthy_dir(mcp_data_dir)
    (base / "BOOTED").write_text(f"{pending}\n", encoding="utf-8")
    pending_path = base / "PENDING"
    if pending_path.is_file():
        pending_path.unlink()


def hold_present(mcp_data_dir: Path) -> bool:
    return (last_healthy_dir(mcp_data_dir) / "hold").is_file()


def snapshot_present(mcp_data_dir: Path) -> bool:
    base = last_healthy_dir(mcp_data_dir)
    if not base.is_dir():
        return False
    if any(p.name.startswith("last-healthy.tmp.") for p in mcp_data_dir.iterdir()):
        return False
    for name in (*SNAPSHOT_ROOTS, "sitecustomize.py"):
        p = base / name
        if name == "sitecustomize.py":
            if not p.is_file():
                return False
        elif not p.is_dir():
            return False
    return True


def docker_inspect_health(container: str = CONTAINER_NAME) -> str:
    """Return docker health/status token: healthy, unhealthy, starting, exited, dead, created, none."""
    proc = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "none"
    status = (proc.stdout or "").strip()
    return status or "none"


def docker_container_image_id(container: str = CONTAINER_NAME) -> str | None:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.Image}}", container],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _starting_elapsed(container: str, *, cap_s: float = HEALTH_WAIT_S) -> bool:
    if docker_inspect_health(container) != "starting":
        return False
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", container],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return True
    started = (proc.stdout or "").strip()
    if not started or started.startswith("0001"):
        return False
    # Rough elapsed: rely on health wait elsewhere; treat unknown as not elapsed.
    return False


def normalize_restore_status(raw: str, *, starting_cap_elapsed: bool) -> str:
    if raw == "starting" and starting_cap_elapsed:
        return "starting_elapsed"
    if raw in ("healthy", "restarting", "running"):
        return raw
    if raw == "none":
        return "none"
    if raw in RESTORE_ELIGIBLE:
        return raw
    return raw


@dataclass(frozen=True, slots=True)
class GateInput:
    mcp_data_dir: Path
    container_status: str
    pending: str | None
    booted: str | None
    hold: bool
    restart_in_progress: bool
    firer: Literal["script", "backup"]


def gate_allows_restore(gate: GateInput) -> bool:
    if gate.hold:
        return False
    if gate.firer == "backup" and gate.restart_in_progress:
        return False
    pending, booted = gate.pending, gate.booted
    if pending is None or booted is None or pending == booted:
        return False
    if gate.container_status == "healthy":
        return False
    if gate.container_status == "restarting":
        return False
    if gate.container_status == "starting":
        return False
    return gate.container_status in RESTORE_ELIGIBLE


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """Ordered docker argv lists the restore path would run."""

    steps: tuple[tuple[str, ...], ...]
    skip_rm: bool


def plan_restore_commands(
    *,
    checked_image_id: str,
    container_exists: bool,
    compose_args: Sequence[str],
) -> RestorePlan:
    steps: list[tuple[str, ...]] = []
    if container_exists:
        steps.append(("docker", "rm", "-f", CONTAINER_NAME))
    steps.append(("docker", "tag", checked_image_id, LOCAL_IMAGE))
    steps.extend(
        [
            (*compose_args, "create", "mcp-server"),
        ]
    )
    return RestorePlan(steps=tuple(steps), skip_rm=not container_exists)


def container_exists(name: str = CONTAINER_NAME) -> bool:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    return any(line.strip() == name for line in (proc.stdout or "").splitlines())


def image_exists(image_ref: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _cold_import_script() -> str:
    lines = [f"import {m}" for m in COLD_IMPORTS]
    return "; ".join(lines)


def run_import_probe(workspace_root: Path, *, image: str = LOCAL_IMAGE) -> int:
    """Run throwaway import probe; return 0 on success, 1 on failure, 2 on timeout."""
    incoming = workspace_root
    vols: list[str] = []
    mounts = [
        (incoming / "libs", "/app/libs"),
        (incoming / "services", "/app/services"),
        (incoming / "config", "/app/config"),
        (incoming / "sitecustomize.py", "/app/sitecustomize.py"),
        (incoming / "pipelines", "/app/pipelines"),
    ]
    for host, cont in mounts:
        if host.exists():
            vols.extend(["-v", f"{host}:{cont}:ro"])
    pl = incoming / "pipelines.local"
    if pl.is_dir():
        vols.extend(["-v", f"{pl}:/app/pipelines.local:ro"])
    tl = incoming / "services/mcp-server/tools/local"
    if tl.is_dir():
        vols.extend(["-v", f"{tl}:/app/services/mcp-server/tools/local:ro"])
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "python",
        *vols,
        "-e",
        "PYTHONPATH=/app/libs",
        "-e",
        "WORKSPACE_ROOT=/app",
        "-w",
        "/app/services/mcp-server",
        image,
        "-c",
        _cold_import_script(),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=PROBE_WALL_S, check=False)
    except subprocess.TimeoutExpired:
        return 2
    return 0 if proc.returncode == 0 else 1


def publish_snapshot_from_container(
    workspace_root: Path, mcp_data_dir: Path, *, container: str = CONTAINER_NAME
) -> int:
    """Copy live COPY set from a healthy container into last-healthy/. Return 0 on success."""
    if docker_inspect_health(container) != "healthy":
        return 0
    tmp = mcp_data_dir / f"last-healthy.tmp.{os.getpid()}"
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    roots = [
        ("libs", "/app/libs"),
        ("services", "/app/services"),
        ("config", "/app/config"),
        ("pipelines", "/app/pipelines"),
    ]
    for name, src in roots:
        dest = tmp / name
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["docker", "cp", f"{container}:{src}/.", str(dest)],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            return 1
    sc = tmp / "sitecustomize.py"
    proc = subprocess.run(
        ["docker", "cp", f"{container}:/app/sitecustomize.py", str(sc)],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    target = last_healthy_dir(mcp_data_dir)
    backup = mcp_data_dir / "last-healthy.prev"
    if target.exists():
        import shutil

        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        target.rename(backup)
    tmp.rename(target)
    booted = read_booted(mcp_data_dir)
    if booted:
        (target / "BOOTED").write_text(f"{booted}\n", encoding="utf-8")
    return 0


def tag_last_healthy_from_container(mcp_data_dir: Path) -> int:
    if docker_inspect_health() != "healthy":
        return 0
    image_id = docker_container_image_id()
    if not image_id:
        return 1
    proc = subprocess.run(
        ["docker", "tag", image_id, LAST_HEALTHY_TAG],
        capture_output=True,
        check=False,
    )
    return proc.returncode


def retag_local_from_last_healthy() -> int:
    if not image_exists(LAST_HEALTHY_TAG):
        return 1
    proc = subprocess.run(
        ["docker", "tag", LAST_HEALTHY_TAG, LOCAL_IMAGE],
        capture_output=True,
        check=False,
    )
    return proc.returncode


def execute_restore(
    *,
    workspace_root: Path,
    mcp_data_dir: Path,
    checked_image_id: str,
    compose_args: list[str],
    firer: Literal["script", "backup"],
    restart_in_progress: Callable[[], bool] | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> int:
    """Run one restore attempt under flock. Return 0 when health wait succeeds."""
    lock_path = last_healthy_lock(mcp_data_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        status = normalize_restore_status(
            docker_inspect_health(), starting_cap_elapsed=_starting_elapsed(CONTAINER_NAME)
        )
        gate = GateInput(
            mcp_data_dir=mcp_data_dir,
            container_status=status,
            pending=read_pending(mcp_data_dir),
            booted=read_booted(mcp_data_dir),
            hold=hold_present(mcp_data_dir),
            restart_in_progress=bool(restart_in_progress and restart_in_progress()),
            firer=firer,
        )
        if not gate_allows_restore(gate):
            return 1
        if not snapshot_present(mcp_data_dir):
            if emit:
                emit("mcp.restart.snapshot.failed", {"reason": "snapshot_missing"})
            return 1
        if not image_exists(checked_image_id):
            if emit:
                emit("mcp.restart.snapshot.failed", {"reason": "image_missing"})
            return 1
        exists = container_exists()
        plan = plan_restore_commands(
            checked_image_id=checked_image_id,
            container_exists=exists,
            compose_args=tuple(compose_args),
        )
        for step in plan.steps:
            proc = subprocess.run(step, capture_output=True, check=False)
            if proc.returncode != 0:
                if emit:
                    emit("mcp.restart.snapshot.failed", {"reason": "docker_step"})
                return 1
        snap = last_healthy_dir(mcp_data_dir)
        for name in SNAPSHOT_ROOTS:
            src = snap / name
            if src.is_dir():
                subprocess.run(
                    ["docker", "cp", f"{src}/.", f"{CONTAINER_NAME}:/app/{name}/"],
                    check=False,
                )
        sc = snap / "sitecustomize.py"
        if sc.is_file():
            subprocess.run(
                ["docker", "cp", str(sc), f"{CONTAINER_NAME}:/app/sitecustomize.py"],
                check=False,
            )
        subprocess.run(["docker", "start", CONTAINER_NAME], check=False)
        if wait_for_healthy(HEALTH_WAIT_S) != 0:
            if emit:
                emit("mcp.restart.snapshot.failed", {"reason": "health_wait"})
            return 1
        if emit:
            emit("mcp.restart.snapshot.restored", {"reason": firer})
        return 0


def wait_for_healthy(timeout_s: int = HEALTH_WAIT_S) -> int:
    start = time.time()
    while True:
        status = docker_inspect_health()
        if status == "healthy":
            return 0
        if status in ("unhealthy", "exited", "dead"):
            return 1
        if time.time() - start >= timeout_s:
            if status == "starting":
                return 1
            return 1
        time.sleep(2)


def mcp_server_entrypoint_probe(root: Path) -> Literal["verified", "contradicted", "indeterminate"]:
    """Container-layout cold imports for land mint (cwd mcp-server, PYTHONPATH=libs only)."""
    mcp_dir = root / "services" / "mcp-server"
    libs = root / "libs"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(libs)
    for module in COLD_IMPORTS:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", f"import {module}"],
                cwd=str(mcp_dir),
                env=env,
                capture_output=True,
                timeout=PROBE_WALL_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "indeterminate"
        if proc.returncode != 0:
            return "contradicted"
    return "verified"


def _compose_args(workspace_root: Path) -> list[str]:
    compose = workspace_root / "docker" / "compose" / "mcp-server.yml"
    args = ["docker", "compose", "-f", str(compose)]
    override = workspace_root / "docker" / "compose" / "mcp-server-browser.override.yml"
    enable = os.environ.get("ENABLE_BROWSER_TOOLS", "").strip().lower()
    if enable in ("1", "true", "yes", "on") and override.is_file():
        args.extend(["-f", str(override)])
    return args


def _cli() -> int:
    parser = argparse.ArgumentParser(description="MCP restart gate CLI")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--mcp-data-dir",
        type=Path,
        default=None,
        help="Defaults to $MCP_DATA_DIR or ~/mcp-data",
    )
    sub = parser.add_subparsers(dest="op", required=True)

    sub.add_parser("source-tree-sha")
    sub.add_parser("snapshot-if-healthy")
    p_probe = sub.add_parser("import-probe")
    p_probe.add_argument("--image", default=LOCAL_IMAGE)
    p_pending = sub.add_parser("write-pending")
    p_pending.add_argument("sha")
    sub.add_parser("promote-booted")
    p_restore = sub.add_parser("restore")
    p_restore.add_argument("--firer", choices=("script", "backup"), default="script")
    p_restore.add_argument("--checked-image-id", required=True)
    p_restore.add_argument(
        "--restart-in-progress",
        action="store_true",
        help="backup firer: treat restart_in_progress as true",
    )
    sub.add_parser("tag-last-healthy")
    sub.add_parser("retag-local-from-last-healthy")
    p_wait = sub.add_parser("wait-healthy")
    p_wait.add_argument("--timeout", type=int, default=HEALTH_WAIT_S)
    sub.add_parser("entrypoint-probe")

    args = parser.parse_args()
    root: Path = args.workspace_root
    mcp_data = args.mcp_data_dir or Path(
        os.environ.get("MCP_DATA_DIR", str(Path.home() / "mcp-data"))
    ).expanduser()

    if args.op == "source-tree-sha":
        print(source_tree_sha(root))
        return 0
    if args.op == "snapshot-if-healthy":
        return publish_snapshot_from_container(root, mcp_data)
    if args.op == "import-probe":
        rc = run_import_probe(root, image=args.image)
        return rc
    if args.op == "write-pending":
        write_pending(mcp_data, args.sha)
        return 0
    if args.op == "promote-booted":
        promote_pending_to_booted(mcp_data)
        return 0
    if args.op == "restore":
        ri = (lambda: True) if args.restart_in_progress else (lambda: False)

        def _emit(signal: str, payload: dict) -> None:
            from deploy_identity.mcp_restart_events import emit_restart_event

            emit_restart_event(signal, payload)

        return execute_restore(
            workspace_root=root,
            mcp_data_dir=mcp_data,
            checked_image_id=args.checked_image_id,
            compose_args=_compose_args(root),
            firer=args.firer,
            restart_in_progress=ri if args.firer == "backup" else None,
            emit=_emit,
        )
    if args.op == "tag-last-healthy":
        return tag_last_healthy_from_container(mcp_data)
    if args.op == "retag-local-from-last-healthy":
        return retag_local_from_last_healthy()
    if args.op == "wait-healthy":
        return wait_for_healthy(args.timeout)
    if args.op == "entrypoint-probe":
        print(mcp_server_entrypoint_probe(root))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
