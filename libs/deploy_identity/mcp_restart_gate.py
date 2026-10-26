"""MCP restart gate — import probe, snapshot, pending/booted, and restore decisions.

The host script ``scripts/sync-and-restart-mcp.sh`` and the manage edge-probe
backup share this module. Docker argv plans are exposed for hermetic tests.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen

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
OPTIONAL_SNAPSHOT_ROOTS = ("pipelines.local",)
SNAPSHOT_CONTROL_FILES = ("hold", "checked_image_id", "BOOTED")
COMPOSE_CREATE_ENV = ("MCP_PROJECT_DIR", "MCP_TASKS_DIR", "MCP_AUTH_TOKEN")
RESTORE_ELIGIBLE = frozenset(
    {"unhealthy", "exited", "dead", "created", "none", "starting_elapsed"}
)


class DockerQueryError(RuntimeError):
    """docker ps or inspect failed other than a missing container.

    Restore treats this as a hard stop. A dead daemon is not an absent
    mcp-server, and proceeding would skip rm and copy onto the bad tree.
    """


def source_tree_sha(workspace_root: Path) -> str:
    """Hash of the MCP COPY set on disk (matches sync script semantics)."""
    paths: list[Path] = []
    for root_name in ("libs", "services/mcp-server"):
        root = workspace_root / root_name.replace("/", os.sep)
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if (
                p.is_file()
                and "__pycache__" not in p.parts
                and not p.name.endswith(".pyc")
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
    """True when last-healthy/ holds the required COPY roots.

    A sibling ``last-healthy.tmp.*`` left by an interrupted publish does not
    hide a complete snapshot. The tmp dir is not the snapshot; only a missing
    or partial ``last-healthy/`` refuses restore.
    """
    base = last_healthy_dir(mcp_data_dir)
    if not base.is_dir():
        return False
    for name in (*SNAPSHOT_ROOTS, "sitecustomize.py"):
        p = base / name
        if name == "sitecustomize.py":
            if not p.is_file():
                return False
        elif not p.is_dir():
            return False
    return True


def _missing_container(stderr: str, stdout: str) -> bool:
    text = f"{stderr}\n{stdout}".lower()
    return "no such object" in text or "no such container" in text


def docker_inspect_health(
    container: str = CONTAINER_NAME, *, strict: bool = False
) -> str:
    """Return docker health/status: healthy, unhealthy, starting, exited, dead, created, none.

    A missing container is ``none``. Any other inspect failure raises
    ``DockerQueryError`` when ``strict`` is set, so restore does not treat a
    dead docker daemon as an absent container. Non-strict callers (snapshot,
    tag) keep the historical ``none`` reading.
    """
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
        if _missing_container(proc.stderr or "", proc.stdout or ""):
            return "none"
        if strict:
            raise DockerQueryError((proc.stderr or "docker inspect failed").strip())
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


def _parse_started_at(value: str) -> float | None:
    text = value.strip()
    if not text or text.startswith("0001"):
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        tz = ""
        for index, char in enumerate(rest):
            if char in "+-":
                tz = rest[index:]
                rest = rest[:index]
                break
        text = f"{head}.{(rest + '000000')[:6]}{tz}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


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
    parsed = _parse_started_at(proc.stdout or "")
    if parsed is None:
        return False
    return (time.time() - parsed) >= cap_s


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
    """Return whether this caller may replace the container from the snapshot.

    A missing ``BOOTED`` file does not block the first restore: that host has
    never promoted a healthy tree. ``PENDING`` must still be present, and
    must differ from ``BOOTED`` when ``BOOTED`` exists. Raw ``starting`` is
    not eligible; the caller passes ``starting_elapsed`` only after the 120s
    cap. ``healthy`` and ``restarting`` never restore.
    """
    if gate.hold:
        return False
    if gate.firer == "backup" and gate.restart_in_progress:
        return False
    if gate.pending is None:
        return False
    if gate.booted is not None and gate.pending == gate.booted:
        return False
    if gate.container_status in ("healthy", "restarting", "starting"):
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
    """Return whether ``name`` is in ``docker ps -a``.

    A non-zero ``docker ps`` raises ``DockerQueryError``. Treating that as
    "absent" skips ``docker rm`` and lets compose create reuse the bad
    writable layer.
    """
    proc = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DockerQueryError((proc.stderr or "docker ps failed").strip())
    return any(line.strip() == name for line in (proc.stdout or "").splitlines())


def backup_create_env_ready(env: Mapping[str, str] | None = None) -> bool:
    """Return whether compose create can interpolate mcp-server.yml.

    Compose create requires ``MCP_PROJECT_DIR``, ``MCP_TASKS_DIR``, and
    ``MCP_AUTH_TOKEN``. The manage backup passes ``build_mcp_env`` output
    here so a missing key refuses before ``docker rm``. When *env* is
    omitted, the process environment is the source (script CLI).
    """
    source = os.environ if env is None else env
    return all(str(source.get(name, "")).strip() for name in COMPOSE_CREATE_ENV)


def read_checked_image_id(mcp_data_dir: Path) -> str | None:
    """Return the image id recorded before the restart window, if the file exists."""
    return _read_sha_file(last_healthy_dir(mcp_data_dir), "checked_image_id")


def resolve_backup_checked_image_id(mcp_data_dir: Path) -> str | None:
    """Return the image the manage backup should restore, never the live container.

    The recorded ``checked_image_id`` file wins. When that file is absent, the
    id behind ``universal-mcp-server:last-healthy`` is the --no-cache rollback
    image. The running container's image is not a source: a failed rebuild
    leaves the bad tree in that image, and copying the snapshot on top keeps
    paths the bad tree added.
    """
    recorded = read_checked_image_id(mcp_data_dir)
    if recorded:
        return recorded
    proc = subprocess.run(
        ["docker", "image", "inspect", "-f", "{{.Id}}", LAST_HEALTHY_TAG],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def image_exists(image_ref: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _cold_import_script() -> str:
    """Return a ``python -c`` body that imports each cold module in a new interpreter.

    A single ``import a; import b`` process keeps later modules warm in
    ``sys.modules`` after ``server`` loads. The container probe must match
    the land probe: four child interpreters, one module each, so a failure
    that appears only in a fresh interpreter is not hidden.
    """
    modules = ", ".join(repr(name) for name in COLD_IMPORTS)
    return (
        "import subprocess, sys; "
        f"mods = ({modules},); "
        "rc = 0\n"
        "for mod in mods:\n"
        "    rc = subprocess.call([sys.executable, '-c', 'import ' + mod])\n"
        "    if rc:\n"
        "        break\n"
        "raise SystemExit(rc)\n"
    )


def _probe_output_detail(proc: subprocess.CompletedProcess[bytes]) -> str:
    raw = proc.stderr or proc.stdout or b""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    if len(text) <= 500:
        return text
    return text[-500:]


def _emit_import_probe(signal: str, detail: str) -> None:
    from deploy_identity.mcp_restart_events import emit_restart_event

    emit_restart_event(signal, {"detail": detail})


_IMAGE_BOOT_PROBE_BODY = (
    "from pathlib import Path\n"
    "text = Path('/app/services/mcp-server/server.py').read_text()\n"
    "if 'mcp.remove_tool(' in text:\n"
    "    raise SystemExit('image server.py uses removed FastMCP.remove_tool API')\n"
    "if 'local_provider.remove_tool' not in text:\n"
    "    raise SystemExit('image server.py missing local_provider.remove_tool prune path')\n"
)


def run_image_boot_probe(*, image: str = LOCAL_IMAGE) -> int:
    """Lint ``server.py`` in the image only (no workspace bind mounts).

    The tree-mounted ``import-probe`` cold-imports modules from the working tree;
    this probe catches stale ``COPY services/`` layers that would crash on first
    boot after ``docker rm`` before ``docker cp`` sync runs.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "python",
        image,
        "-c",
        _IMAGE_BOOT_PROBE_BODY,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=PROBE_WALL_S, check=False
        )
    except subprocess.TimeoutExpired:
        _emit_import_probe("mcp.restart.check.timeout", "image boot probe timed out")
        return 2
    if proc.returncode != 0:
        _emit_import_probe(
            "mcp.restart.check.failed",
            _probe_output_detail(proc) or "image boot probe failed",
        )
        return 1
    return 0


def run_import_probe(workspace_root: Path, *, image: str = LOCAL_IMAGE) -> int:
    """Run the throwaway import probe in *image*.

    Returns 0 on success, 1 on a non-zero child, and 2 when the docker
    client exceeds ``PROBE_WALL_S``. Failure emits ``mcp.restart.check.failed``
    and a timeout emits ``mcp.restart.check.timeout``. The ``-c`` body is
    ``_cold_import_script`` (one interpreter per module).
    """
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
        proc = subprocess.run(
            cmd, capture_output=True, timeout=PROBE_WALL_S, check=False
        )
    except subprocess.TimeoutExpired:
        _emit_import_probe("mcp.restart.check.timeout", "import probe timed out")
        return 2
    if proc.returncode != 0:
        _emit_import_probe(
            "mcp.restart.check.failed",
            _probe_output_detail(proc) or "import probe failed",
        )
        return 1
    return 0


def _copy_container_tree(container: str, src: str, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["docker", "cp", f"{container}:{src}/.", str(dest)],
        capture_output=True,
        check=False,
    )
    return proc.returncode


def publish_snapshot_from_container(
    workspace_root: Path, mcp_data_dir: Path, *, container: str = CONTAINER_NAME
) -> int:
    """Copy the live COPY set from a healthy container into last-healthy/.

    Returns 0 on success or when the container is not healthy (nothing to
    publish). Replacing the directory keeps ``hold``, ``checked_image_id``,
    and ``BOOTED`` from the previous snapshot. ``pipelines.local`` is copied
    when the container has it and omitted when that cp fails. Required roots
    still fail the publish.
    """
    del workspace_root
    if docker_inspect_health(container) != "healthy":
        return 0
    import shutil

    tmp = mcp_data_dir / f"last-healthy.tmp.{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    roots = [
        ("libs", "/app/libs"),
        ("services", "/app/services"),
        ("config", "/app/config"),
        ("pipelines", "/app/pipelines"),
    ]
    for name, src in roots:
        if _copy_container_tree(container, src, tmp / name) != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return 1
    for name in OPTIONAL_SNAPSHOT_ROOTS:
        dest = tmp / name
        if _copy_container_tree(container, f"/app/{name}", dest) != 0:
            shutil.rmtree(dest, ignore_errors=True)
    sc = tmp / "sitecustomize.py"
    proc = subprocess.run(
        ["docker", "cp", f"{container}:/app/sitecustomize.py", str(sc)],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    target = last_healthy_dir(mcp_data_dir)
    saved: dict[str, bytes] = {}
    if target.is_dir():
        for name in SNAPSHOT_CONTROL_FILES:
            path = target / name
            if path.is_file():
                saved[name] = path.read_bytes()
    backup = mcp_data_dir / "last-healthy.prev"
    if target.exists():
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        target.rename(backup)
    tmp.rename(target)
    for name, data in saved.items():
        (target / name).write_bytes(data)
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


def _fail_restore(emit: Callable[[str, dict], None] | None, reason: str) -> int:
    if emit:
        emit("mcp.restart.snapshot.failed", {"reason": reason})
    return 1


def _copy_snapshot_into_container(snap: Path) -> int:
    names = [*SNAPSHOT_ROOTS, *OPTIONAL_SNAPSHOT_ROOTS]
    for name in names:
        src = snap / name
        if not src.is_dir():
            if name in OPTIONAL_SNAPSHOT_ROOTS:
                continue
            return 1
        proc = subprocess.run(
            ["docker", "cp", f"{src}/.", f"{CONTAINER_NAME}:/app/{name}/"],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return 1
    sc = snap / "sitecustomize.py"
    if not sc.is_file():
        return 1
    proc = subprocess.run(
        ["docker", "cp", str(sc), f"{CONTAINER_NAME}:/app/sitecustomize.py"],
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
    env: Mapping[str, str] | None = None,
) -> int:
    """Run one restore attempt under flock. Return 0 when the health wait succeeds.

    A failed ``docker ps`` or ``docker inspect``, a backup environment that
    cannot compose-create, a failed snapshot copy, or a failed ``docker start``
    returns 1 before any later step. The backup firer checks *env* (or the
    process environment when *env* is omitted) before ``docker rm`` so a
    missing ``MCP_PROJECT_DIR`` does not delete the container it cannot
    recreate. Docker steps, including compose create, receive *env* so
    mcp-server.yml interpolates the mcp.yaml export set rather than manage's
    own process environment.
    """
    del workspace_root
    lock_path = last_healthy_lock(mcp_data_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            raw = docker_inspect_health(strict=True)
        except DockerQueryError:
            return _fail_restore(emit, "docker_inspect_failed")
        status = normalize_restore_status(
            raw, starting_cap_elapsed=_starting_elapsed(CONTAINER_NAME)
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
            return _fail_restore(emit, "snapshot_missing")
        if not image_exists(checked_image_id):
            return _fail_restore(emit, "image_missing")
        if firer == "backup" and not backup_create_env_ready(env):
            return _fail_restore(emit, "compose_env_missing")
        try:
            exists = container_exists()
        except DockerQueryError:
            return _fail_restore(emit, "docker_ps_failed")
        plan = plan_restore_commands(
            checked_image_id=checked_image_id,
            container_exists=exists,
            compose_args=tuple(compose_args),
        )
        for step in plan.steps:
            proc = subprocess.run(step, capture_output=True, check=False, env=env)
            if proc.returncode != 0:
                return _fail_restore(emit, "docker_step")
        if _copy_snapshot_into_container(last_healthy_dir(mcp_data_dir)) != 0:
            return _fail_restore(emit, "snapshot_copy")
        start = subprocess.run(
            ["docker", "start", CONTAINER_NAME],
            capture_output=True,
            check=False,
        )
        if start.returncode != 0:
            return _fail_restore(emit, "docker_start")
        if wait_for_healthy(HEALTH_WAIT_S) != 0:
            return _fail_restore(emit, "health_wait")
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


def read_container_stamp_sha(container: str = CONTAINER_NAME) -> str | None:
    from deploy_identity.code_version import read_stamp_sha

    proc = subprocess.run(
        ["docker", "exec", container, "cat", "/app/.source_sync_stamp"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as tmp:
        tmp.write(proc.stdout)
        path = Path(tmp.name)
    try:
        return read_stamp_sha(path)
    finally:
        path.unlink(missing_ok=True)


def fetch_mcp_health_code_version(timeout_s: float = 5.0) -> str | None:
    from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url

    url = resolve_mcp_health_probe_url()
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cv = data.get("code_version")
    if not isinstance(cv, str) or not cv.strip():
        return None
    return cv.strip().lower()


def emit_direct_sync_success_events(*, no_cache: bool, image_id: str | None) -> None:
    from deploy_identity.mcp_restart_events import emit_restart_event

    stamp_sha = read_container_stamp_sha() or ""
    health_code_version = fetch_mcp_health_code_version() or ""
    base: dict[str, object] = {
        "stamp_sha": stamp_sha,
        "health_code_version": health_code_version,
        "no_cache": no_cache,
    }
    if image_id:
        base["image_id"] = image_id
    emit_restart_event("mcp.restart.ok", dict(base))
    if stamp_sha and health_code_version and stamp_sha != health_code_version:
        emit_restart_event("mcp.restart.seal_mismatch", dict(base))


def mcp_server_entrypoint_probe(
    root: Path,
) -> Literal["verified", "contradicted", "indeterminate"]:
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
    p_boot = sub.add_parser("image-boot-probe")
    p_boot.add_argument("--image", default=LOCAL_IMAGE)
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
    p_log = sub.add_parser("emit-log")
    p_log.add_argument("message")
    p_sync = sub.add_parser("emit-direct-sync-success")
    p_sync.add_argument("--no-cache", action="store_true")
    p_sync.add_argument("--image-id", default="")

    args = parser.parse_args()
    root: Path = args.workspace_root
    mcp_data = (
        args.mcp_data_dir
        or Path(
            os.environ.get("MCP_DATA_DIR", str(Path.home() / "mcp-data"))
        ).expanduser()
    )

    if args.op == "source-tree-sha":
        print(source_tree_sha(root))
        return 0
    if args.op == "snapshot-if-healthy":
        return publish_snapshot_from_container(root, mcp_data)
    if args.op == "import-probe":
        rc = run_import_probe(root, image=args.image)
        return rc
    if args.op == "image-boot-probe":
        return run_image_boot_probe(image=args.image)
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
    if args.op == "emit-log":
        from deploy_identity.mcp_restart_events import emit_restart_event

        emit_restart_event("mcp.restart.log", {"detail": args.message})
        return 0
    if args.op == "emit-direct-sync-success":
        image_id = args.image_id.strip() or None
        emit_direct_sync_success_events(no_cache=bool(args.no_cache), image_id=image_id)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
