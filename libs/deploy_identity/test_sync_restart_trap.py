"""Trap integration tests for scripts/sync-and-restart-mcp.sh (stub docker on PATH)."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from deploy_identity.mcp_env_keys import config_env_key_names, environment_key_names

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "sync-and-restart-mcp.sh"


def _stub_docker_dir(
    tmp_path: Path,
    *,
    stop_fails: bool = False,
    start_fails: bool = False,
    inspect_health: str = "healthy",
    post_restart_health: str | None = None,
    container_exists: bool = True,
    kill_on_run: bool = False,
    kill_on_stop: bool = False,
    import_probe_fails: bool = False,
    compose_fails: bool = False,
    kill_before_stamp_write: bool = False,
    stamp_exec_fails: bool = False,
    stamp_restore_fails: bool = False,
    exec_true_fails: bool = False,
    rollback_services_cp_fails: bool = False,
    kill_on_sync_cp: bool = False,
    materialize_host_cp: bool = True,
    nonce_mode: str = "stored",
    prior_stamp: str = "2020-01-01T00:00:00Z\noldsha\n",
    stamp_rm_fails: bool = False,
    sync_cp_fails: bool = False,
    tree_rm_fails: bool = False,
    inspect_mounts_fail: bool = False,
    mount_dest: str = "",
    listing_orphan: str = "",
    snapshot_pipelines_local_fails: bool = False,
    sync_services_cp_fails: bool = False,
    optional_root_remains: bool = False,
    mount_secret_container: str = "",
    inspect_env_keys: tuple[str, ...] | None = None,
    health_none_after_rm: bool = False,
) -> Path:
    if inspect_env_keys is None:
        chosen_env_keys = tuple(
            sorted(
                environment_key_names(
                    _REPO_ROOT / "docker" / "compose" / "mcp-server.yml"
                )
            )
        )
    else:
        chosen_env_keys = inspect_env_keys
    env_printf = " ".join(
        shlex.quote(f"{key}=envkey-sentinel") for key in chosen_env_keys
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    nonce_file = tmp_path / "nonce"
    stamp_file = tmp_path / "container_stamp"
    health_phase = tmp_path / "post_restart"
    prior_stamp_file = tmp_path / "prior_stamp"
    prior_stamp_file.write_text(prior_stamp, encoding="utf-8")
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> {log}
        health_phase="{health_phase}"
        stamp_file="{stamp_file}"
        prior_stamp_file="{prior_stamp_file}"
        case "$1" in
          ps)
            if [[ -f "{tmp_path / "mcp_removed"}" ]]; then
              exit 0
            fi
            if [[ "$2" == "-a" ]]; then
              if [[ "{str(container_exists).lower()}" == "true" ]]; then
                echo "mcp-server"
              fi
              exit 0
            fi
            if [[ "{str(container_exists).lower()}" == "true" ]]; then
              echo "mcp-server"
            fi
            exit 0
            ;;
          inspect)
            if [[ "$*" == *"Mounts"* ]]; then
              if [[ "{str(inspect_mounts_fail).lower()}" == "true" ]]; then
                exit 1
              fi
              printf '%s\\n' "{mount_dest}"
              exit 0
            fi
            if [[ "$*" == *"Health.Status"* ]] || [[ "$*" == *"State.Health"* ]]; then
              if [[ -f "{tmp_path / "health_none_after_rm"}" ]]; then
                echo none
                exit 0
              fi
              if [[ -f "{tmp_path / "health_exited"}" ]]; then
                echo exited
                exit 0
              fi
              if [[ -f "$health_phase" && "{post_restart_health or ""}" != "" ]]; then
                echo "{post_restart_health or inspect_health}"
              else
                echo "{inspect_health}"
              fi
              exit 0
            fi
            if [[ "$*" == *".Image"* ]]; then
              echo "sha256:deadbeef"
              exit 0
            fi
            if [[ "$*" == *"Config.Env"* ]]; then
              printf '%s\\n' {env_printf}
              exit 0
            fi
            echo "running"
            exit 0
            ;;
          stop)
            if [[ "{str(kill_on_stop).lower()}" == "true" ]]; then
              kill -TERM "$PPID" 2>/dev/null || true
            fi
            if [[ "{str(stop_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            exit 0
            ;;
          start)
            if [[ "{str(start_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            printf '%s' post_restart > "$health_phase"
            exit 0
            ;;
          run)
            if [[ "{str(import_probe_fails).lower()}" == "true" && "$*" == *"--network"*"none"* ]]; then
              exit 1
            fi
            if [[ "{str(kill_on_run).lower()}" == "true" ]]; then
              script_pid=$(ps -o ppid= -p "$PPID" | tr -d ' ')
              if [[ -r "/proc/${{script_pid}}/cmdline" ]] && grep -a -q sync-and-restart "/proc/${{script_pid}}/cmdline"; then
                kill -TERM "$script_pid" 2>/dev/null || true
              fi
            fi
            exit 0
            ;;
          exec)
            joined="$*"
            nonce_file={str(nonce_file)!r}
            if [[ "{str(kill_on_sync_cp).lower()}" == "true" && "$joined" == *"tar -xf - -C /app"* ]]; then
              kill -TERM "$PPID" 2>/dev/null || true
              exit 1
            fi
            if [[ "$joined" == *"tar -xf - -C /app"* ]]; then
              cat > /dev/null
              tar_n_file={tmp_path / "tar_exec.n"}
              n=$(cat "$tar_n_file" 2>/dev/null || echo 0)
              n=$((n + 1))
              echo "$n" > "$tar_n_file"
              echo SYNC_TAR_EXEC >> {log}
              if [[ "{str(sync_cp_fails).lower()}" == "true" && "$n" -eq 1 ]]; then
                : > "{tmp_path / "health_exited"}"
                exit 1
              fi
              if [[ "{str(sync_services_cp_fails).lower()}" == "true" && "$n" -eq 2 ]]; then
                exit 1
              fi
              exit 0
            fi
            if [[ "$joined" == *"sha256sum /app/"* ]]; then
              path="${{joined#*sha256sum }}"
              path="${{path%% *}}"
              if [[ -f "{tmp_path / "mount_secret"}" && "{mount_secret_container}" == "$path" ]]; then
                printf '%s  secret\\n' "$(sha256sum "{tmp_path / "mount_secret"}" | awk '{{print $1}}')"
              else
                printf '%s  stub\\n' "$(printf stub | sha256sum | awk '{{print $1}}')"
              fi
              exit 0
            fi
            if [[ "$joined" == *" true" ]]; then
              if [[ "{str(exec_true_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              exit 0
            fi
            if [[ "$joined" == *"test -e /app/"* ]]; then
              if [[ "{str(optional_root_remains).lower()}" == "true" ]]; then
                exit 0
              fi
              target="${{joined##*test -e }}"
              target="${{target%% *}}"
              if [[ -f "{tmp_path / "tree_rm.log"}" ]] && grep -q -F "D ${{target}}" "{tmp_path / "tree_rm.log"}"; then
                exit 1
              fi
              exit 0
            fi
            if [[ "$joined" == *"test -f /app/.source_sync_stamp"* ]]; then
              if [[ -f "$stamp_file" || -s "$prior_stamp_file" ]]; then
                exit 0
              fi
              exit 1
            fi
            if [[ "$joined" == *"cat /app/.source_sync_stamp"* ]]; then
              if [[ -f "$stamp_file" ]]; then
                cat "$stamp_file"
              elif [[ -f "$prior_stamp_file" ]]; then
                cat "$prior_stamp_file"
              fi
              exit 0
            fi
            if [[ "$joined" == *".source_sync_stamp"* && "$joined" == *"printf"* ]]; then
              if [[ "{str(kill_before_stamp_write).lower()}" == "true" ]]; then
                kill -PIPE "$PPID" 2>/dev/null || true
                exit 1
              fi
              if [[ "{str(stamp_exec_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              echo STAMP_WRITE >> {log}
              needle="> /app/.source_sync_stamp"
              if [[ "$joined" == *"$needle"* ]]; then
                rest="${{joined#*printf}}"
                printf '%s' "$rest" > "$stamp_file"
              fi
            fi
            if [[ "$joined" == *".source_sync_stamp"* && "$joined" == *"cat > /app/.source_sync_stamp"* ]]; then
              if [[ "{str(stamp_restore_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              echo STAMP_RESTORE >> {log}
              cat > "$stamp_file"
            fi
            if [[ "$3" == "find" ]]; then
              if [[ -n "{listing_orphan}" ]]; then
                printf '%s\\n' "{listing_orphan}"
              fi
              base="${{MCP_DATA_DIR}}/last-healthy"
              for arg in "$@"; do
                if [[ "$arg" == /app/* ]]; then
                  rel="${{arg#/app/}}"
                  dir="$base/$rel"
                  if [[ -d "$dir" ]]; then
                    find "$dir" \\( -name '__pycache__' -o -name '*.pyc' \\) -prune -o -printf "/app/${{rel}}/%P\\n"
                  fi
                fi
              done
              exit 0
            fi
            if [[ "$joined" == *"rm -f /app/.source_sync_stamp"* ]]; then
              echo STAMP_RM >> {log}
              if [[ "{str(stamp_rm_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              rm -f "$stamp_file"
              exit 0
            fi
            if [[ "$joined" == *"while IFS= read"* ]]; then
              echo TREE_RM >> {log}
              cat >> "{tmp_path / "tree_rm.log"}"
              if [[ -n "{mount_secret_container}" && -f "{tmp_path / "mount_secret"}" ]]; then
                secret="{mount_secret_container}"
                while IFS= read -r line; do
                  path="${{line#* }}"
                  if [[ "$secret" == "$path" || "$secret" == "$path"/* ]]; then
                    rm -f "{tmp_path / "mount_secret"}"
                  fi
                done < "{tmp_path / "tree_rm.log"}"
              fi
              if [[ "{str(tree_rm_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              exit 0
            fi
            if [[ "$joined" == *".source_sync_nonce"* ]]; then
              if [[ "$joined" == *"cat /app/.source_sync_nonce"* ]]; then
                if [[ "{nonce_mode}" == "wrong" ]]; then
                  printf '%s\\n' "not-the-nonce"
                elif [[ "{nonce_mode}" == "stored" && -f "$nonce_file" ]]; then
                  cat "$nonce_file"
                fi
                exit 0
              fi
              needle="printf '%s\\\\n' '"
              rest="${{joined#*"$needle"}}"
              if [[ "$rest" != "$joined" ]]; then
                nonce=${{rest%%\\'*}}
                printf '%s\\n' "$nonce" > "$nonce_file"
              fi
            fi
            exit 0
            ;;
          compose)
            if [[ "{str(compose_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            if [[ "$*" == *"mcp-server"* ]]; then
              rm -f "{tmp_path / "mcp_removed"}" "{tmp_path / "health_none_after_rm"}"
            fi
            exit 0
            ;;
          cp)
            dest="${{@: -1}}"
            if [[ "{str(kill_on_sync_cp).lower()}" == "true" && "$dest" == "mcp-server:/app/libs/" && "$*" != *last-healthy* ]]; then
              kill -TERM "$PPID" 2>/dev/null || true
              exit 1
            fi
            if [[ "{str(rollback_services_cp_fails).lower()}" == "true" && "$dest" == "mcp-server:/app/services/" && "$*" == *last-healthy* ]]; then
              exit 1
            fi
            if [[ "{str(snapshot_pipelines_local_fails).lower()}" == "true" && "$*" == *mcp-server:/app/pipelines.local* && "$dest" != *:* ]]; then
              exit 1
            fi
            if [[ "{str(sync_services_cp_fails).lower()}" == "true" && "$dest" == "mcp-server:/app/services/" && "$*" != *last-healthy* ]]; then
              exit 1
            fi
            if [[ "{str(sync_cp_fails).lower()}" == "true" && "$dest" == "mcp-server:/app/libs/" && "$*" != *last-healthy* ]]; then
              : > "{tmp_path / "health_exited"}"
              exit 1
            fi
            if [[ "{str(materialize_host_cp).lower()}" == "true" && "$dest" != *:* ]]; then
              if [[ "$dest" == *.py ]]; then
                mkdir -p "$(dirname "$dest")"
                printf '%s\\n' oldsha > "$dest"
              else
                mkdir -p "$dest"
                printf '%s\\n' oldsha > "$dest/marker"
              fi
            fi
            exit 0
            ;;
          rm)
            for arg in "$@"; do
              if [[ "$arg" == "mcp-server" ]]; then
                : > "{tmp_path / "mcp_removed"}"
              fi
            done
            if [[ "{str(health_none_after_rm).lower()}" == "true" ]]; then
              : > "{tmp_path / "health_none_after_rm"}"
            fi
            exit 0
            ;;
          tag|image) exit 0 ;;
          *) exit 0 ;;
        esac
        """
    )
    stub = bin_dir / "docker"
    stub.write_text(script)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _minimal_env(tmp_path: Path, stub_bin: Path) -> dict[str, str]:
    mcp_yaml = tmp_path / "mcp.yaml"
    mcp_yaml.write_text(
        textwrap.dedent(
            f"""\
            auth_token: test-token
            project_dir: {tmp_path / "proj"}
            data_dir: {tmp_path / "mcp-data"}
            mcp_server_url: http://127.0.0.1/mcp
            """
        )
    )
    (tmp_path / "proj").mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"
    env["MCP_YAML"] = str(mcp_yaml)
    env["HOME"] = str(tmp_path)
    env["EMAIL_BRIDGE_SOCK"] = "/nonexistent"
    env["PYTHONPATH"] = str(_REPO_ROOT / "libs")
    mktemp_dir = tmp_path / "mktemp"
    mktemp_dir.mkdir(exist_ok=True)
    env["TMPDIR"] = str(mktemp_dir)
    return env


def _python() -> str:
    return os.environ.get("PYTHON", str(Path.home() / ".venvs/universal/bin/python"))


def _run_sync_script(
    tmp_path: Path,
    stub: Path,
    *args: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = _python()
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    first = env["PATH"].split(":")[0]
    if not os.access(Path(first) / "docker", os.X_OK):
        raise AssertionError(f"docker stub is not first on PATH: {first}")
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _docker_log(tmp_path: Path) -> str:
    log = tmp_path / "docker.log"
    if not log.is_file():
        return ""
    return log.read_text(encoding="utf-8")


def _log_indices(log: str) -> dict[str, int]:
    stamp_write = log.find("STAMP_WRITE")
    search_from = stamp_write if stamp_write >= 0 else 0
    tail = log[search_from:]
    stop_match = re.search(r"^stop ", tail, re.MULTILINE)
    start_match = re.search(r"^start ", tail, re.MULTILINE)
    return {
        "stamp_write": stamp_write,
        "stamp_restore": log.find("STAMP_RESTORE"),
        "stop": search_from + stop_match.start() if stop_match else -1,
        "start": search_from + start_match.start() if start_match else -1,
    }


def _seed_checked_image(tmp_path: Path) -> None:
    data = tmp_path / "mcp-data" / "last-healthy"
    data.mkdir(parents=True, exist_ok=True)
    (data / "checked_image_id").write_text("sha256:deadbeef\n", encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_routine_path_writes_stamp_before_stop_start(tmp_path: Path) -> None:
    """AC1: stamp write precedes docker stop/start and is not repeated after healthy."""
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="stored")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    idx = _log_indices(log)
    assert proc.returncode == 0, proc.stderr
    assert idx["stamp_write"] >= 0, log
    assert log.count("STAMP_WRITE") == 1, log
    assert idx["stop"] >= 0 and idx["start"] >= 0, log
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log


def _compose_env_key_tuple() -> tuple[str, ...]:
    return tuple(
        sorted(
            environment_key_names(_REPO_ROOT / "docker" / "compose" / "mcp-server.yml")
        )
    )


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_equal_env_key_names_keep_stop_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config.Env names that cover the passthrough set keep docker stop/start.

    Breaks when a routine sync recreates even though every compose env key
    build_mcp_env would pass is already on the container. Inspect values
    must not be echoed.
    """
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "fixture-token-not-secret")
    keys = _compose_env_key_tuple()
    assert "VORTEX_BEARER_TOKENS" in keys
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", inspect_env_keys=keys)
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    idx = _log_indices(log)
    combined = proc.stdout + proc.stderr + log
    assert proc.returncode == 0, proc.stderr
    assert "rm -f mcp-server" not in log
    assert idx["stop"] >= 0 and idx["start"] >= 0, log
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log
    assert "envkey-sentinel" not in combined
    assert "fixture-token-not-secret" not in combined
    assert "missing env key names" not in proc.stdout


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_env_key_name_diff_recreates_before_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passthrough key missing from Config.Env recreates before docker cp.

    Breaks when stop/start keeps create-time Config.Env after build_mcp_env
    gains a key name. Recreate after the cp would wipe the synced tree.
    """
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "fixture-token-not-secret")
    keys = tuple(
        key for key in _compose_env_key_tuple() if key != "VORTEX_BEARER_TOKENS"
    )
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", inspect_env_keys=keys)
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    combined = proc.stdout + proc.stderr + log
    assert proc.returncode == 0, proc.stderr
    assert "MCP container missing env key names: " in proc.stdout
    assert "VORTEX_BEARER_TOKENS" in proc.stdout
    assert "fixture-token-not-secret" not in combined
    assert "envkey-sentinel" not in combined
    rm_at = log.find("rm -f mcp-server")
    first_cp_after_rm = log.find("SYNC_TAR_EXEC", rm_at)
    if first_cp_after_rm < 0:
        first_cp_after_rm = log.find("mcp-server:/app/libs/", rm_at)
    up_no_start = log.find("up -d --no-start mcp-server", rm_at)
    assert "SYNC_TAR_ROOT libs" in proc.stdout, proc.stdout
    stop_tail = log[first_cp_after_rm:] if first_cp_after_rm >= 0 else log
    stop_match = re.search(r"^stop ", stop_tail, re.MULTILINE)
    assert rm_at >= 0, log
    assert first_cp_after_rm > rm_at, log
    assert up_no_start >= 0 and up_no_start < first_cp_after_rm, log
    assert stop_match is not None, log
    assert first_cp_after_rm + stop_match.start() > first_cp_after_rm, log


def test_env_inspect_splits_names_inside_docker() -> None:
    """Config.Env values never leave docker inspect, and a bare name is a key.

    Breaks when the compare writes KEY=VALUE lines to a temp file, or when a
    line with no '=' is dropped.
    """
    body = _extract_bash_function("container_passthrough_env_keys_differ")
    assert '(split . "=")' in body
    assert "mktemp" not in body
    assert config_env_key_names("TOKEN=super-secret\nPLAIN\n") == {
        "TOKEN",
        "PLAIN",
    }


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_env_key_recreate_compose_failure_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed compose up after env-key rm runs plan_restore_commands.

    Breaks when rm happens before write-pending: pending is absent, restore
    refuses, and mcp stays gone. Health must be none after rm or a healthy
    inspect also refuses restore.
    """
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "fixture-token-not-secret")
    keys = tuple(
        key for key in _compose_env_key_tuple() if key != "VORTEX_BEARER_TOKENS"
    )
    _seed_checked_image(tmp_path)
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        inspect_health="healthy",
        inspect_env_keys=keys,
        compose_fails=True,
        health_none_after_rm=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    combined = proc.stdout + proc.stderr + log
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "create mcp-server" in log, log
    assert "fixture-token-not-secret" not in combined
    assert "super-secret" not in combined


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_restores_snapshotted_stamp(tmp_path: Path) -> None:
    """AC4: docker stop failure restores the pre-write stamp snapshot."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        prior_stamp="2020-01-01T00:00:00Z\noldsha\n",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "STAMP_RESTORE" in log, log
    assert log.count("STAMP_WRITE") == 1, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_start_failure_keeps_new_stamp_without_gate_restore(tmp_path: Path) -> None:
    """Start failure disarms EXIT trap; gate restore must not run (review condition 1)."""
    stub = _stub_docker_dir(tmp_path, start_fails=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert log.count("STAMP_WRITE") == 1, log
    assert "STAMP_RESTORE" not in log, log
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr + proc.stdout


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_kill_during_stop_restores_snapshotted_stamp(tmp_path: Path) -> None:
    """TERM during docker stop, before stop is recorded, restores the snapshot."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        kill_on_stop=True,
        prior_stamp="2020-01-01T00:00:00Z\noldsha\n",
    )
    proc = _run_sync_script(tmp_path, stub, timeout=90)
    log = _docker_log(tmp_path)
    assert proc.returncode == -signal.SIGTERM, (proc.returncode, proc.stderr[-400:])
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_never_healthy_single_pre_restart_stamp_write(tmp_path: Path) -> None:
    """Never-healthy path: one pre-restart stamp write, then a restore that returns 1.

    Post-restart inspect is unhealthy, so the gate's health wait fails and
    the success marker is not printed.
    """
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        inspect_health="healthy",
        post_restart_health="unhealthy",
        nonce_mode="stored",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert log.count("STAMP_WRITE") == 1, log
    idx = _log_indices(log)
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr


def _seed_last_healthy_copy_set(tmp_path: Path, token: str = "oldsha") -> None:
    base = tmp_path / "mcp-data" / "last-healthy"
    for name in ("libs", "services", "config", "pipelines"):
        dest = base / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "marker").write_text(token + "\n", encoding="utf-8")
    (base / "sitecustomize.py").write_text(token + "\n", encoding="utf-8")


def _stub_build_bash(stub: Path) -> None:
    stub_bash = stub / "bash"
    stub_bash.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == *build-mcp.sh* || "${2:-}" == *build-mcp.sh* ]]; then\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/bash "$@"\n'
    )
    stub_bash.chmod(stub_bash.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_container_create_compose_failure_runs_gate_restore(tmp_path: Path) -> None:
    """Compose create failure reaches gate restore, which refuses before rm.

    The success marker is printed only after a restore that returns 0. A
    healthy inspect refuses, so the disagree marker is the result.
    """
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        container_exists=False,
        compose_fails=True,
        inspect_health="healthy",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "rm -f mcp-server" not in log, log
    assert "STAMP_RESTORE" not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_nocache_compose_up_failure_runs_gate_restore(tmp_path: Path) -> None:
    """--no-cache compose up failure reaches a gate restore that refuses.

    The container inspect is still healthy, so restore returns before
    ``docker rm`` and the success marker is not printed.
    """
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        container_exists=True,
        compose_fails=True,
        inspect_health="healthy",
    )
    _stub_build_bash(stub)
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = _python()
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--no-cache"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr[-800:]
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-800:]
    assert "STAMP_WRITE" not in log, log
    assert "STAMP_RESTORE" not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_concurrent_sync_exits_while_lock_held(tmp_path: Path) -> None:
    """A second run exits non-zero while the MCP_DATA_DIR lock is held."""
    lock = tmp_path / "mcp-data" / "sync-and-restart-mcp.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    holder = subprocess.Popen(
        ["flock", str(lock), "bash", "-c", "echo ready; sleep 30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    try:
        stub = _stub_docker_dir(tmp_path, inspect_health="healthy")
        proc = _run_sync_script(tmp_path, stub, timeout=30)
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert proc.returncode != 0, proc.stdout
    assert "another sync-and-restart-mcp.sh is already running" in proc.stderr


def _assert_mktemp_removed(tmp_path: Path) -> None:
    leftover = list((tmp_path / "mktemp").iterdir())
    assert leftover == [], leftover


def _assert_tree_rolled_back(log: str, tmp_path: Path) -> None:
    for name in ("libs", "services", "config", "pipelines"):
        assert f"{tmp_path}/mcp-data/last-healthy/{name}/." in log, log
        assert f"mcp-server:/app/{name}/" in log, log
    assert f"{tmp_path}/mcp-data/last-healthy/sitecustomize.py" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_while_healthy_rolls_back_tree_and_stamp(tmp_path: Path) -> None:
    """Stop fails, container stays healthy: copy last-healthy back, then the old stamp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, stop_fails=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr
    _assert_tree_rolled_back(log, tmp_path)
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_while_healthy_without_copy_set_marks_disagreement(
    tmp_path: Path,
) -> None:
    """No last-healthy copy set: leave the tree and say stamp and tree disagree."""
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        materialize_host_cp=False,
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr
    assert "last-healthy/libs/." not in log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_services_cp_failure_restores_stamp_and_disagrees(
    tmp_path: Path,
) -> None:
    """A later last-healthy cp fails after libs succeeded: mixed tree, old stamp, disagree."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        rollback_services_cp_fails=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr
    assert "last-healthy/libs/." in log, log
    assert "last-healthy/services/." in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_restore_failure_after_tree_rollback_does_not_claim_success(
    tmp_path: Path,
) -> None:
    """Tree rollback succeeded but the stamp write failed: no success marker."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        stamp_restore_fails=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_snapshot_exec_failure_after_sync_does_not_claim_gate_restore(
    tmp_path: Path,
) -> None:
    """Snapshot exec fails after the tree sync: no gate-restore marker on a healthy container."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, exec_true_fails=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "STAMP_WRITE" not in log, log
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_sigterm_during_sync_cp_marks_tree_outcome(tmp_path: Path) -> None:
    """SIGTERM during source cp, before the stamp window, still settles the tree."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, kill_on_sync_cp=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub, timeout=90)
    assert proc.returncode != 0, proc.stdout
    log = _docker_log(tmp_path)
    assert "exec mcp-server find" in log and "/app/libs" in log, log[-400:]
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_sigpipe_between_snapshot_and_stamp_write_restores(tmp_path: Path) -> None:
    """SIGPIPE after the snapshot and before the stamp write restores and drops mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, kill_before_stamp_write=True)
    proc = _run_sync_script(tmp_path, stub, timeout=90)
    log = _docker_log(tmp_path)
    assert "STAMP_WRITE" not in log, log
    assert "STAMP_RESTORE" in log, log
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    _assert_mktemp_removed(tmp_path)


def _install_failing_tool(stub: Path, name: str) -> None:
    tool = stub / name
    tool.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)


def _install_git_fail_on_second_head_revparse(stub: Path, tmp_path: Path) -> None:
    """Fail the second bash ``git -C … rev-parse HEAD`` (stamp write, not pre-sync snapshot)."""
    real_git = shutil.which("git") or "/usr/bin/git"
    counter = tmp_path / "git-rev-parse-head.count"
    tool = stub / "git"
    tool.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [[ "$1" == "-C" && "$3" == "rev-parse" && "$4" == "HEAD" ]]; then
              ppid_cmd=$(tr '\\0' ' ' < /proc/$PPID/cmdline 2>/dev/null || true)
              if [[ "$ppid_cmd" != *python* ]]; then
                n=$(cat {shlex.quote(str(counter))} 2>/dev/null || echo 0)
                n=$((n + 1))
                echo "$n" > {shlex.quote(str(counter))}
                if [[ "$n" -ge 2 ]]; then
                  exit 1
                fi
              fi
            fi
            exec {shlex.quote(real_git)} "$@"
            """
        ),
        encoding="utf-8",
    )
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_write_git_failure_restores(tmp_path: Path) -> None:
    """git failure inside the stamp write restores the snapshot and removes mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path)
    _install_git_fail_on_second_head_revparse(stub, tmp_path)
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_RESTORE" in log, log
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_write_sha256sum_failure_restores(tmp_path: Path) -> None:
    """sha256sum failure in the stamp write restores the snapshot and removes mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path)
    _install_failing_tool(stub, "sha256sum")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_RESTORE" in log, log
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_write_chown_failure_restores(tmp_path: Path) -> None:
    """docker exec chown failure inside the stamp write restores and removes mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, stamp_exec_fails=True)
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_WRITE" not in log, log
    assert "STAMP_RESTORE" in log, log
    _assert_mktemp_removed(tmp_path)


def test_import_probe_refusal_skips_stamp_and_stop(tmp_path: Path) -> None:
    """AC5: import probe failure exits before cp/stop/stamp writes."""
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", import_probe_fails=True)
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "STAMP_WRITE" not in log, log
    assert "stop " not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_healthy_finish_no_trap_restore(tmp_path: Path) -> None:
    """Healthy restart exits 0 and does not rm the container.

    The stub's ``exec cat`` echoes the nonce written by ``exec sh -c printf``,
    so verify passes after health returns 0. A disarm that never ran would
    take the EXIT handler and rm.
    """
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="stored")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Verified sync nonce survived restart" in proc.stdout
    assert "rm -f mcp-server" not in log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_verify_fail_after_healthy_does_not_restore(tmp_path: Path) -> None:
    """Health 0 then a bad nonce exits 1 with no restore argv.

    The EXIT trap is already disarmed, so a verify failure must not rm or
    compose-create the container.
    """
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="wrong")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rm -f mcp-server" not in log
    assert "compose" not in log


def test_restore_handler_second_entry_is_noop(tmp_path: Path) -> None:
    """Script-level re-entry of the EXIT handler does not restore twice."""
    function = _extract_bash_function("_restore_window_exit")
    log = tmp_path / "restore.log"
    body = "\n".join(
        [
            "set +e",
            "shopt -s extdebug",
            "trap 'if [[ \"$BASH_COMMAND\" == exit\\ * ]]; then builtin return 0; fi' DEBUG",
            "CHECKED_IMAGE_ID=sha256:abc",
            "CHECKED_IMAGE_FILE=",
            "_RESTORE_HANDLER_RAN=0",
            "_STOP_SUCCEEDED=0",
            "_START_FAILED=0",
            "_STAMP_WRITTEN=0",
            "_SOURCE_SYNC_STAMP_SNAPSHOT=/nonexistent",
            "_cleanup_stamp_snapshot() { :; }",
            "gate() { printf '%s\\n' \"$*\" >> " + str(log) + "; }",
            "restore_source_sync_stamp_from_snapshot() { :; }",
            function,
            "_restore_window_exit",
            "_restore_window_exit",
            "",
        ]
    )
    proc = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
    )
    recorded = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert proc.returncode == 0, proc.stderr
    assert recorded.count("restore") == 1, recorded


def _extract_bash_function(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    marker = f"{name}() {{"
    start = text.index(marker)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"{name} not closed in {_SCRIPT}")


def test_trap_handler_pipe_trap_allows_restore_after_failed_echo() -> None:
    """Binding (a): the script EXIT handler ignores a real broken-pipe echo."""
    function = _extract_bash_function("_restore_window_exit")
    body = "\n".join(
        [
            "set -euo pipefail",
            'exec 3>&"$PIPE_FD"',
            "CHECKED_IMAGE_ID=sha256:abc",
            "CHECKED_IMAGE_FILE=",
            "_RESTORE_HANDLER_RAN=0",
            "_STOP_SUCCEEDED=0",
            "_START_FAILED=0",
            "_STAMP_WRITTEN=0",
            "_SOURCE_SYNC_STAMP_SNAPSHOT=/nonexistent",
            "_cleanup_stamp_snapshot() { :; }",
            "restore_source_sync_stamp_from_snapshot() { :; }",
            "gate() {",
            "  echo restore-byte >&3",
            "  echo RESTORE_RAN",
            "}",
            function,
            "trap _restore_window_exit EXIT",
            "false",
            "",
        ]
    )
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    os.set_inheritable(write_fd, True)
    env = os.environ.copy()
    env["PIPE_FD"] = str(write_fd)
    proc = subprocess.run(
        ["bash", "-c", body],
        pass_fds=(write_fd,),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    os.close(write_fd)
    assert "Broken pipe" in proc.stderr
    assert "RESTORE_RAN" in proc.stdout
    assert proc.returncode == 1


def test_probe_pinned_before_container_create_path(tmp_path: Path) -> None:
    """Binding (b): kill the script after the snapshot and before compose create."""
    stub = _stub_docker_dir(
        tmp_path,
        container_exists=False,
        inspect_health="healthy",
        kill_on_run=True,
    )
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = os.environ.get(
        "PYTHON", str(Path.home() / ".venvs/universal/bin/python")
    )
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    log = (
        (tmp_path / "docker.log").read_text(encoding="utf-8")
        if (tmp_path / "docker.log").is_file()
        else ""
    )
    cp_idx = log.find("cp ")
    run_idx = log.find("run ")
    assert cp_idx >= 0, log
    assert run_idx >= 0, log
    assert run_idx > cp_idx, log
    assert proc.returncode == -signal.SIGTERM, (proc.returncode, proc.stderr[-400:])
    assert "compose" not in log, log


def test_nocache_path_writes_stamp_before_stop_start(tmp_path: Path) -> None:
    """AC6: --no-cache path writes stamp then docker stop/start before healthy."""
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="stored")
    stub_bash = stub / "bash"
    stub_bash.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == *build-mcp.sh* || "$2" == *build-mcp.sh* ]]; then\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/bash "$@"\n'
    )
    stub_bash.chmod(stub_bash.stat().st_mode | stat.S_IXUSR)
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = _python()
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    env["PATH"] = f"{stub}:{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--no-cache"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    log = _docker_log(tmp_path)
    idx = _log_indices(log)
    assert proc.returncode == 0, proc.stderr + proc.stdout[-500:]
    assert log.count("STAMP_WRITE") == 1, log
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log


def test_nocache_unhealthy_does_not_start_build(tmp_path: Path) -> None:
    """S2(5): --no-cache does not start the build when mcp-server is unhealthy."""
    stub = _stub_docker_dir(tmp_path, inspect_health="unhealthy", container_exists=True)
    bash_log = tmp_path / "bash.log"
    stub_bash = stub / "bash"
    stub_bash.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == *build-mcp.sh* || "$2" == *build-mcp.sh* ]]; then\n'
        f'  echo "BUILD $*" >> {bash_log}\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/bash "$@"\n'
    )
    stub_bash.chmod(stub_bash.stat().st_mode | stat.S_IXUSR)
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = str(Path.home() / ".venvs/universal/bin/python")
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    env["PATH"] = f"{stub}:{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--no-cache"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    recorded = bash_log.read_text(encoding="utf-8") if bash_log.is_file() else ""
    assert "build-mcp.sh" not in recorded
    assert proc.returncode != 0


def _tree_rm_text(tmp_path: Path) -> str:
    path = tmp_path / "tree_rm.log"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_absent_stamp_rm_failure_does_not_claim_rollback(tmp_path: Path) -> None:
    """No prior stamp and a failed rm after tree rollback must not print the success marker.

    The container stays healthy, the copy set rolls back, and the new stamp
    remains because ``docker exec rm`` exits 1. Callers are the trap tests.
    """
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        prior_stamp="",
        stamp_rm_fails=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "STAMP_RM" in log, log
    assert "source_basis=working_tree" in (tmp_path / "container_stamp").read_text(
        encoding="utf-8"
    )


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_source_cp_failure_exited_does_not_claim_gate_restore(tmp_path: Path) -> None:
    """A failed source cp with the container exited, before write-pending, is not a restore.

    Gate restore refuses while PENDING is absent, so the success marker must
    stay absent and ``docker rm`` must not run.
    """
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        sync_cp_fails=True,
        inspect_health="healthy",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "STAMP_WRITE" not in log, log
    assert "rm -f mcp-server" not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_exited_without_snapshot_does_not_claim_gate_restore(
    tmp_path: Path,
) -> None:
    """Stop fails, the container is exited, and last-healthy has no copy set.

    The prior stamp is restored, and a gate that returns before ``docker rm``
    must not print the gate-restore marker.
    """
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="exited",
        materialize_host_cp=False,
        prior_stamp="2020-01-01T00:00:00Z\noldsha\n",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "rm -f mcp-server" not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_removes_new_module_and_package_dir(tmp_path: Path) -> None:
    """A module and a package directory this sync added are deleted, then rollback is claimed.

    Both paths are absent from last-healthy. The package directory must be in
    the delete list so an empty directory cannot remain as a namespace package.
    """
    module = _REPO_ROOT / "libs" / "_f6_probe_mod.py"
    package = _REPO_ROOT / "libs" / "_f6_probe_pkg"
    module.write_text("probe = 1\n", encoding="utf-8")
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    try:
        _seed_last_healthy_copy_set(tmp_path)
        stub = _stub_docker_dir(tmp_path, stop_fails=True, inspect_health="healthy")
        proc = _run_sync_script(tmp_path, stub)
        rm_text = _tree_rm_text(tmp_path)
        assert "F /app/libs/_f6_probe_mod.py" in rm_text, rm_text[-400:]
        assert "D /app/libs/_f6_probe_pkg" in rm_text, rm_text[-400:]
        assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    finally:
        module.unlink(missing_ok=True)
        shutil.rmtree(package, ignore_errors=True)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_delete_failure_disagrees(tmp_path: Path) -> None:
    """A failed delete of a synced extra stops the rollback claim.

    The container is still healthy and the copy set copied back. Callers must
    see the disagree marker and no success marker.
    """
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        tree_rm_fails=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-500:]
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr
    assert "TREE_RM" in _docker_log(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_candidate_under_mount_disagrees(tmp_path: Path) -> None:
    """A candidate at or under a mount destination deletes nothing.

    Mount ``/app/libs`` covers synced extras. The disagree marker is the
    result, and the tree-rm exec must not run.
    """
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        mount_dest="/app/libs",
    )
    proc = _run_sync_script(tmp_path, stub)
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr[-500:]
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-500:]
    assert "TREE_RM" not in _docker_log(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_mount_inspect_failure_disagrees(tmp_path: Path) -> None:
    """A non-zero mount inspect deletes nothing and prints the disagree marker.

    The container is still healthy with a complete last-healthy copy set.
    """
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        inspect_mounts_fail=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr[-500:]
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-500:]
    assert "TREE_RM" not in _docker_log(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_listing_orphan_disagrees(tmp_path: Path) -> None:
    """A pre-existing orphan in the container listing blocks the success marker.

    The orphan is not in the sync source list, so the delete set must not
    include it, and no further delete follows the mismatch.
    """
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        listing_orphan="/app/libs/orphan.py",
    )
    proc = _run_sync_script(tmp_path, stub)
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr[-500:]
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-500:]
    assert "/app/libs/orphan.py" not in _tree_rm_text(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_removes_pipelines_local_root(tmp_path: Path) -> None:
    """A synced pipelines.local absent from last-healthy is removed as one directory.

    Child paths are not a separate delete list. The success marker follows
    only when the container listing then matches last-healthy.
    """
    local = _REPO_ROOT / "pipelines.local"
    local.mkdir()
    (local / "probe.txt").write_text("probe\n", encoding="utf-8")
    try:
        _seed_last_healthy_copy_set(tmp_path)
        stub = _stub_docker_dir(
            tmp_path,
            stop_fails=True,
            inspect_health="healthy",
            snapshot_pipelines_local_fails=True,
        )
        proc = _run_sync_script(tmp_path, stub)
        rm_text = _tree_rm_text(tmp_path)
        assert "D /app/pipelines.local\n" in rm_text, rm_text[-400:]
        assert "pipelines.local/probe.txt" not in rm_text
        assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    finally:
        shutil.rmtree(local, ignore_errors=True)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_failed_services_cp_stays_in_rollback_candidate_set(tmp_path: Path) -> None:
    """A services docker cp that returns 1 still puts that root in the delete set.

    The container stays healthy and last-healthy is complete. The probe file
    is in the workspace source list and absent from last-healthy, so a rollback
    that never noted services cannot claim the tree matches.
    """
    probe = _REPO_ROOT / "services" / "_probe_svcfail.py"
    probe.write_text("probe = 1\n", encoding="utf-8")
    try:
        _seed_last_healthy_copy_set(tmp_path)
        stub = _stub_docker_dir(
            tmp_path,
            inspect_health="healthy",
            sync_services_cp_fails=True,
        )
        proc = _run_sync_script(tmp_path, stub)
        log = _docker_log(tmp_path)
        rm_text = _tree_rm_text(tmp_path)
        assert "F /app/services/_probe_svcfail.py" in rm_text, rm_text[-300:]
        assert "find /app/libs /app/services" in log, log[-400:]
        assert proc.returncode != 0, proc.stdout
    finally:
        probe.unlink(missing_ok=True)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_never_healthy_gate_marker_follows_restore_result(tmp_path: Path) -> None:
    """An unhealthy restart prints the gate marker only when restore returns 0.

    The checked image is set and the later inspect is unhealthy, so the gate
    reaches docker rm and then fails the health wait. The marker must not
    precede that failure.
    """
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        inspect_health="healthy",
        post_restart_health="unhealthy",
        nonce_mode="stored",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "rm -f mcp-server" in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_optional_root_remaining_after_delete_disagrees(tmp_path: Path) -> None:
    """A noted pipelines.local that is still in the container is not a match.

    last-healthy has no pipelines.local directory, the delete command returns 0,
    and the container path remains. An empty find argument list must not
    report success.
    """
    local = _REPO_ROOT / "pipelines.local"
    local.mkdir()
    (local / "probe.txt").write_text("probe\n", encoding="utf-8")
    try:
        _seed_last_healthy_copy_set(tmp_path)
        stub = _stub_docker_dir(
            tmp_path,
            stop_fails=True,
            inspect_health="healthy",
            snapshot_pipelines_local_fails=True,
            optional_root_remains=True,
        )
        proc = _run_sync_script(tmp_path, stub)
        assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr[
            -500:
        ]
        assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-500:]
    finally:
        shutil.rmtree(local, ignore_errors=True)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_candidate_ancestor_of_mount_disagrees_without_deleting(
    tmp_path: Path,
) -> None:
    """A candidate directory above a mount is not deleted.

    Services is noted, the services copy does not run, and last-healthy has
    no ``services/newpkg``. The mount is ``/app/services/newpkg/vol`` with a
    file at ``vol/secret``. ``rm -rf`` of the ancestor would remove that file.
    """
    package = _REPO_ROOT / "services" / "newpkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    secret = tmp_path / "mount_secret"
    secret.write_text("secret\n", encoding="utf-8")
    try:
        _seed_last_healthy_copy_set(tmp_path)
        stub = _stub_docker_dir(
            tmp_path,
            inspect_health="healthy",
            sync_services_cp_fails=True,
            mount_dest="/app/services/newpkg/vol",
            mount_secret_container="/app/services/newpkg/vol/secret",
        )
        proc = _run_sync_script(tmp_path, stub)
        assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr[-500:]
        assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr
        assert _tree_rm_text(tmp_path) == ""
        assert secret.read_text(encoding="utf-8") == "secret\n"
        assert "TREE_RM" not in _docker_log(tmp_path)
    finally:
        shutil.rmtree(package, ignore_errors=True)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_mount_name_prefix_sibling_is_not_an_ancestor(tmp_path: Path) -> None:
    """``/app/services/new`` is not an ancestor of ``/app/services/newpkg/vol``.

    A shared name prefix without a slash boundary must not abort the delete
    of the sibling. The mount file under newpkg is not a candidate here.
    """
    sibling = _REPO_ROOT / "services" / "new"
    sibling.mkdir()
    (sibling / "keep.py").write_text("keep = 1\n", encoding="utf-8")
    try:
        _seed_last_healthy_copy_set(tmp_path)
        stub = _stub_docker_dir(
            tmp_path,
            stop_fails=True,
            inspect_health="healthy",
            mount_dest="/app/services/newpkg/vol",
        )
        proc = _run_sync_script(tmp_path, stub)
        rm_text = _tree_rm_text(tmp_path)
        assert "F /app/services/new/keep.py" in rm_text, rm_text[-200:]
        assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-400:]
    finally:
        shutil.rmtree(sibling, ignore_errors=True)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_dry_run_reports_mount_inside_synced_tree(tmp_path: Path) -> None:
    """A bind mount under /app/libs is excluded from the copy set (--dry-run).

    Breaks when wholesale docker cp overwrites a busy single-file bind mount.
    """
    mount_rel = "libs/cursor_bridge/cdp_gateway_forwarder.py"
    hub_file = _REPO_ROOT / mount_rel
    hub_file.parent.mkdir(parents=True, exist_ok=True)
    hub_file.write_text("hub-forwarder\n", encoding="utf-8")
    secret = tmp_path / "mount_secret"
    secret.write_text("hub-forwarder\n", encoding="utf-8")
    try:
        stub = _stub_docker_dir(
            tmp_path,
            inspect_health="healthy",
            mount_dest="/app/libs/cursor_bridge/cdp_gateway_forwarder.py",
            mount_secret_container="/app/libs/cursor_bridge/cdp_gateway_forwarder.py",
        )
        proc = _run_sync_script(tmp_path, stub, "--dry-run")
        log = _docker_log(tmp_path)
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, combined
        assert "MCP_SYNC_DRY_RUN" in combined, combined
        assert "/app/libs/cursor_bridge/cdp_gateway_forwarder.py" in combined, combined
        assert "MOUNT_HASH libs/cursor_bridge/cdp_gateway_forwarder.py: MATCH" in combined
        assert "Copy set paths" in combined
        assert "STAMP_WRITE" not in log, log
        assert "stop " not in log, log
    finally:
        hub_file.unlink(missing_ok=True)
