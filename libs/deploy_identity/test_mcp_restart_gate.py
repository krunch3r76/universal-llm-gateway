"""Hermetic tests for MCP restart gate decisions and docker argv plans."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from deploy_identity.mcp_restart_gate import (
    LAST_HEALTHY_TAG,
    LOCAL_IMAGE,
    GateInput,
    _starting_elapsed,
    execute_restore,
    gate_allows_restore,
    plan_restore_commands,
    publish_snapshot_from_container,
    read_booted,
    read_pending,
    snapshot_present,
    source_tree_sha,
    write_pending,
)

pytestmark = pytest.mark.offline


def test_gate_refuses_when_pending_equals_booted(tmp_path: Path) -> None:
    base = tmp_path / "last-healthy"
    base.mkdir()
    (base / "PENDING").write_text("abc\n")
    (base / "BOOTED").write_text("abc\n")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="unhealthy",
        pending=read_pending(tmp_path),
        booted=read_booted(tmp_path),
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is False


def test_gate_allows_unhealthy_pending_mismatch(tmp_path: Path) -> None:
    write_pending(tmp_path, "incoming")
    (tmp_path / "last-healthy" / "BOOTED").write_text("old\n")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="unhealthy",
        pending=read_pending(tmp_path),
        booted=read_booted(tmp_path),
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is True


def test_gate_refuses_healthy_container(tmp_path: Path) -> None:
    write_pending(tmp_path, "incoming")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="healthy",
        pending=read_pending(tmp_path),
        booted=None,
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is False


def test_gate_backup_respects_restart_in_progress(tmp_path: Path) -> None:
    write_pending(tmp_path, "incoming")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="exited",
        pending=read_pending(tmp_path),
        booted="old",
        hold=False,
        restart_in_progress=True,
        firer="backup",
    )
    assert gate_allows_restore(gate) is False


def test_plan_restore_no_rm_when_container_absent() -> None:
    plan = plan_restore_commands(
        checked_image_id="sha256:abc",
        container_exists=False,
        compose_args=("docker", "compose", "-f", "mcp-server.yml"),
    )
    assert plan.skip_rm is True
    assert not any(step[:3] == ("docker", "rm", "-f") for step in plan.steps)
    assert ("docker", "tag", "sha256:abc", "universal-mcp-server:local") in plan.steps


def test_plan_restore_includes_rm_when_container_present() -> None:
    plan = plan_restore_commands(
        checked_image_id="sha256:abc",
        container_exists=True,
        compose_args=("docker", "compose", "-f", "mcp-server.yml"),
    )
    assert plan.skip_rm is False
    assert plan.steps[0] == ("docker", "rm", "-f", "mcp-server")


def test_snapshot_present_requires_roots(tmp_path: Path) -> None:
    base = tmp_path / "last-healthy"
    base.mkdir()
    assert snapshot_present(tmp_path) is False
    for name in ("libs", "services", "config", "pipelines"):
        (base / name).mkdir()
    (base / "sitecustomize.py").write_text("# stub\n")
    assert snapshot_present(tmp_path) is True


def test_source_tree_sha_stable_empty_libs(tmp_path: Path) -> None:
    (tmp_path / "libs").mkdir()
    (tmp_path / "services" / "mcp-server").mkdir(parents=True)
    h1 = source_tree_sha(tmp_path)
    h2 = source_tree_sha(tmp_path)
    assert h1 == h2


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeDocker:
    """In-memory docker for restore tests. Compose create reuses a live container."""

    def __init__(self) -> None:
        self.health = "unhealthy"
        self.ps_rc = 0
        self.inspect_rc = 0
        self.inspect_stderr = "Error: docker daemon unavailable"
        self.container_present = True
        self.container_files: set[str] = set()
        self.images: dict[str, set[str]] = {}
        self.tags: dict[str, str] = {LOCAL_IMAGE: "sha256:good"}
        self.commands: list[tuple[str, ...]] = []
        self.cp_fail_substr = ""
        self.start_rc = 0
        self.live_image = "sha256:good"
        self.started = False

    def __call__(self, args: list[str] | tuple[str, ...], **_kwargs: object) -> _Proc:
        argv = tuple(str(part) for part in args)
        self.commands.append(argv)
        if not argv or argv[0] != "docker":
            return _Proc(0)
        verb = argv[1]
        if verb == "ps":
            if self.ps_rc != 0:
                return _Proc(self.ps_rc, "", "docker ps failed")
            names = "mcp-server\n" if self.container_present else ""
            return _Proc(0, names, "")
        if verb == "inspect":
            return self._inspect(argv)
        if verb == "image":
            return self._image(argv)
        if verb == "rm":
            self.container_present = False
            self.container_files = set()
            self.started = False
            return _Proc(0)
        if verb == "tag":
            src, dest = argv[2], argv[3]
            self.tags[dest] = self.tags.get(src, src)
            return _Proc(0)
        if verb == "compose" and "create" in argv:
            if self.container_present:
                return _Proc(0)
            image = self.tags.get(LOCAL_IMAGE, self.live_image)
            self.container_files = set(self.images.get(image, set()))
            self.container_present = True
            self.health = "created"
            return _Proc(0)
        if verb == "cp":
            return self._cp(argv[2], argv[3])
        if verb == "start":
            if self.start_rc != 0:
                return _Proc(self.start_rc, "", "start failed")
            self.started = True
            self.health = "healthy"
            return _Proc(0)
        return _Proc(0)

    def _inspect(self, argv: tuple[str, ...]) -> _Proc:
        joined = " ".join(argv)
        target = argv[-1]
        if "StartedAt" in joined:
            return _Proc(0, "2019-01-01T00:00:00.000000000Z\n", "")
        if target == "mcp-server" and (".Image" in joined):
            if self.inspect_rc != 0 and "No such" not in self.inspect_stderr:
                return _Proc(self.inspect_rc, "", self.inspect_stderr)
            return _Proc(0, self.live_image + "\n", "")
        if "Health" in joined or "State.Status" in joined:
            if self.inspect_rc != 0:
                return _Proc(self.inspect_rc, "", self.inspect_stderr)
            return _Proc(0, self.health + "\n", "")
        return _Proc(0, "running\n", "")

    def _image(self, argv: tuple[str, ...]) -> _Proc:
        ref = argv[-1]
        ident = self.tags.get(ref, ref if ref in self.images else "")
        if not ident:
            return _Proc(1, "", "no such image")
        if "-f" in argv:
            return _Proc(0, ident + "\n", "")
        return _Proc(0, "", "")

    def _cp(self, spec_src: str, spec_dest: str) -> _Proc:
        if self.cp_fail_substr and self.cp_fail_substr in (spec_src + spec_dest):
            return _Proc(1, "", "cp failed")
        if spec_src.startswith("mcp-server:"):
            dest = Path(spec_dest)
            if spec_src.endswith("/."):
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".from-container").write_text("ok\n", encoding="utf-8")
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("ok\n", encoding="utf-8")
            return _Proc(0)
        if not self.container_present:
            return _Proc(1, "", "no container")
        host = Path(spec_src[:-2] if spec_src.endswith("/.") else spec_src)
        cont_root = spec_dest.split(":", 1)[1]
        if host.is_dir():
            for path in host.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(host).as_posix()
                    self.container_files.add(cont_root.rstrip("/") + "/" + rel)
        elif host.is_file():
            self.container_files.add(cont_root)
        return _Proc(0)


def _arm_restore_tree(tmp_path: Path) -> None:
    base = tmp_path / "last-healthy"
    for name in ("libs", "services", "config", "pipelines"):
        directory = base / name
        directory.mkdir(parents=True)
        (directory / "keep.py").write_text("keep\n", encoding="utf-8")
    (base / "sitecustomize.py").write_text("# stub\n", encoding="utf-8")
    (base / "PENDING").write_text("incoming\n", encoding="utf-8")
    (base / "BOOTED").write_text("old\n", encoding="utf-8")


def _restore(
    tmp_path: Path, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch, **kwargs: object
) -> int:
    monkeypatch.setattr("deploy_identity.mcp_restart_gate.subprocess.run", fake)
    options = {
        "workspace_root": tmp_path,
        "mcp_data_dir": tmp_path,
        "checked_image_id": "sha256:good",
        "compose_args": ["docker", "compose", "-f", "mcp-server.yml"],
        "firer": "script",
    }
    options.update(kwargs)
    return execute_restore(**options)  # type: ignore[arg-type]


def test_starting_with_cap_elapsed_triggers_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S2(1): starting past the 120s cap is a restore status."""

    def fake_run(args: list[str], **_kwargs: object) -> _Proc:
        joined = " ".join(str(part) for part in args)
        if "StartedAt" in joined:
            return _Proc(0, "2019-01-01T00:00:00.000000000Z\n", "")
        if "Health" in joined or "State.Status" in joined:
            return _Proc(0, "starting\n", "")
        return _Proc(1, "", "unexpected")

    monkeypatch.setattr("deploy_identity.mcp_restart_gate.subprocess.run", fake_run)
    assert _starting_elapsed("mcp-server", cap_s=120) is True
    assert _starting_elapsed("mcp-server", cap_s=10**12) is False
    write_pending(tmp_path, "incoming")
    (tmp_path / "last-healthy" / "BOOTED").write_text("old\n", encoding="utf-8")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="starting_elapsed",
        pending=read_pending(tmp_path),
        booted=read_booted(tmp_path),
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is True


def test_missing_booted_does_not_disable_first_restore(tmp_path: Path) -> None:
    """S2(2): a host that has never promoted BOOTED can still restore."""
    write_pending(tmp_path, "incoming")
    assert read_booted(tmp_path) is None
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="unhealthy",
        pending=read_pending(tmp_path),
        booted=read_booted(tmp_path),
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is True


def _publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "last-healthy"
    (base / "libs").mkdir(parents=True)
    (base / "libs" / "old.py").write_text("old\n", encoding="utf-8")
    (base / "hold").write_text("stop\n", encoding="utf-8")
    (base / "checked_image_id").write_text("sha256:good\n", encoding="utf-8")
    (base / "BOOTED").write_text("oldsha\n", encoding="utf-8")
    fake = FakeDocker()
    fake.health = "healthy"
    monkeypatch.setattr("deploy_identity.mcp_restart_gate.subprocess.run", fake)
    assert publish_snapshot_from_container(tmp_path, tmp_path) == 0


def test_publish_snapshot_keeps_hold_and_checked_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2(3): replacing last-healthy/ must not drop the operator hold or checked id."""
    _publish(tmp_path, monkeypatch)
    base = tmp_path / "last-healthy"
    assert not (base / "libs" / "old.py").exists()
    assert (base / "libs" / ".from-container").is_file()
    assert (base / "hold").read_text(encoding="utf-8") == "stop\n"
    assert (base / "checked_image_id").read_text(encoding="utf-8") == "sha256:good\n"
    assert (base / "BOOTED").read_text(encoding="utf-8") == "oldsha\n"


def test_stale_last_healthy_tmp_does_not_block_restore(tmp_path: Path) -> None:
    """S2(4): an interrupted publish's tmp dir must not freeze later restores."""
    base = tmp_path / "last-healthy"
    for name in ("libs", "services", "config", "pipelines"):
        (base / name).mkdir(parents=True)
    (base / "sitecustomize.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "last-healthy.tmp.4242").mkdir()
    assert snapshot_present(tmp_path) is True


def test_publish_snapshot_includes_pipelines_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2(6): the snapshot copy set includes pipelines.local when the container has it."""
    _publish(tmp_path, monkeypatch)
    marker = tmp_path / "last-healthy" / "pipelines.local" / ".from-container"
    assert marker.is_file()


def test_restore_reports_failure_when_snapshot_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3: a failed snapshot docker cp is a failed restore, not a continued start."""
    _arm_restore_tree(tmp_path)
    fake = FakeDocker()
    fake.images["sha256:good"] = {"/app/libs/keep.py"}
    fake.cp_fail_substr = "/app/libs"
    waits = {"n": 0}

    def _wait(_timeout: int = 120) -> int:
        waits["n"] += 1
        return 0

    monkeypatch.setattr("deploy_identity.mcp_restart_gate.wait_for_healthy", _wait)
    rc = _restore(tmp_path, fake, monkeypatch)
    assert rc == 1
    assert not any(cmd[1] == "start" for cmd in fake.commands)
    assert waits["n"] == 0


def test_restore_reports_failure_when_docker_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3: docker start's exit status fails the restore before the health wait."""
    _arm_restore_tree(tmp_path)
    fake = FakeDocker()
    fake.images["sha256:good"] = {"/app/libs/keep.py"}
    fake.start_rc = 1
    waits = {"n": 0}

    def _wait(_timeout: int = 120) -> int:
        waits["n"] += 1
        return 0

    monkeypatch.setattr("deploy_identity.mcp_restart_gate.wait_for_healthy", _wait)
    rc = _restore(tmp_path, fake, monkeypatch)
    assert rc == 1
    assert waits["n"] == 0


def test_backup_refuses_before_rm_when_project_dir_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4: missing MCP_PROJECT_DIR refuses before docker rm."""
    _arm_restore_tree(tmp_path)
    fake = FakeDocker()
    fake.images["sha256:good"] = {"/app/libs/keep.py"}
    fake.container_files = {"/app/libs/keep.py", "/app/libs/ghost_added.py"}
    monkeypatch.delenv("MCP_PROJECT_DIR", raising=False)
    monkeypatch.setenv("MCP_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    rc = _restore(tmp_path, fake, monkeypatch, firer="backup")
    assert rc == 1
    assert not any(cmd[1] == "rm" for cmd in fake.commands)
    assert "/app/libs/ghost_added.py" in fake.container_files


def test_failed_docker_ps_or_inspect_refuses_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1: docker ps or inspect failure is not 'container absent'."""
    _arm_restore_tree(tmp_path)
    ghost = "/app/libs/ghost_added.py"
    for ps_rc, inspect_rc in ((1, 0), (0, 1)):
        fake = FakeDocker()
        fake.images["sha256:good"] = {"/app/libs/keep.py"}
        fake.container_files = {"/app/libs/keep.py", ghost}
        fake.ps_rc = ps_rc
        fake.inspect_rc = inspect_rc
        rc = _restore(tmp_path, fake, monkeypatch)
        assert rc == 1
        assert not any(cmd[1] == "rm" for cmd in fake.commands)
        assert not any(cmd[1] == "cp" for cmd in fake.commands)


def test_r2_added_path_absent_on_main_backup_and_docker_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: a path the bad tree added is not in the restored container.

    Main drops a writable-layer file via rm + create. Backup creates from the
    recorded checked id (the :last-healthy id when that file is absent), not
    the live image. A failed docker ps refuses instead of copying onto the
    existing tree.
    """
    ghost = "/app/libs/ghost_added.py"
    _arm_restore_tree(tmp_path)
    main = FakeDocker()
    main.images["sha256:good"] = {"/app/libs/keep.py"}
    main.container_files = {"/app/libs/keep.py", ghost}
    rc_main = _restore(tmp_path, main, monkeypatch)
    assert rc_main == 0
    assert ghost not in main.container_files

    (tmp_path / "last-healthy" / "checked_image_id").write_text(
        "sha256:good\n", encoding="utf-8"
    )
    backup = FakeDocker()
    backup.live_image = "sha256:bad"
    backup.images["sha256:good"] = {"/app/libs/keep.py"}
    backup.images["sha256:bad"] = {"/app/libs/keep.py", ghost}
    backup.tags[LOCAL_IMAGE] = "sha256:bad"
    backup.tags[LAST_HEALTHY_TAG] = "sha256:good"
    backup.container_files = set(backup.images["sha256:bad"])
    monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr("deploy_identity.mcp_restart_gate.subprocess.run", backup)
    from scripts.model_manager.ui.controller.mcp_edge_probe_loop import McpEdgeProbeLoop

    asyncio.run(McpEdgeProbeLoop(workspace_root=tmp_path)._maybe_backup_restore())
    assert ghost not in backup.container_files
    assert backup.started is True

    (tmp_path / "last-healthy" / "checked_image_id").unlink()
    tagged = FakeDocker()
    tagged.live_image = "sha256:bad"
    tagged.images["sha256:good"] = {"/app/libs/keep.py"}
    tagged.images["sha256:bad"] = {"/app/libs/keep.py", ghost}
    tagged.tags[LOCAL_IMAGE] = "sha256:bad"
    tagged.tags[LAST_HEALTHY_TAG] = "sha256:good"
    tagged.container_files = set(tagged.images["sha256:bad"])
    monkeypatch.setattr("deploy_identity.mcp_restart_gate.subprocess.run", tagged)
    asyncio.run(McpEdgeProbeLoop(workspace_root=tmp_path)._maybe_backup_restore())
    assert ghost not in tagged.container_files

    errored = FakeDocker()
    errored.images["sha256:good"] = {"/app/libs/keep.py"}
    errored.container_files = {"/app/libs/keep.py", ghost}
    errored.ps_rc = 1
    rc_err = _restore(tmp_path, errored, monkeypatch)
    assert rc_err == 1
    assert not any(cmd[1] == "cp" for cmd in errored.commands)
    assert ghost not in (errored.container_files if errored.started else set())
