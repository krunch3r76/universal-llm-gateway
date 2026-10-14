"""Launch guarded manage reexec as a transient user systemd service."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from transport_utils import MANAGE_SOCKET

from .seat_prelude import seat_operator_home, seat_python_bin

HUB_ROOT = Path("/mnt/torus/projects/universal-llm-gateway")
RESULT_PATH = HUB_ROOT / "tmp" / "guarded-manage-reexec-result.json"
LOG_PATH = HUB_ROOT / "tmp" / "guarded-manage-reexec.log"
UNIT_PREFIX = "ulg-manage-reexec-"
TMUX_BIN = "/usr/bin/tmux"

RunCmd = Callable[..., subprocess.CompletedProcess[str]]

_ENV_LOCAL_KEYS = frozenset({"MANAGE_SOCKET", "GATEWAY_DIR"})


def _utc_compact_unit_suffix() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def unit_name_for_launch() -> str:
    return f"{UNIT_PREFIX}{_utc_compact_unit_suffix()}.service"


def read_env_local_subset(hub: Path) -> dict[str, str]:
    """Return only MANAGE_SOCKET and GATEWAY_DIR from hub ``.env.local`` when present."""
    path = hub / ".env.local"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key not in _ENV_LOCAL_KEYS:
            continue
        value = value.strip().strip("'\"")
        if value:
            out[key] = value
    return out


def parse_active_reexec_units(list_units_stdout: str) -> list[str]:
    """Parse ``systemctl list-units --state=active,activating`` rows for reexec units."""
    active: list[str] = []
    for line in list_units_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name = line.split(None, 1)[0]
        if name.startswith(UNIT_PREFIX):
            active.append(name)
    return active


def launcher_env_for_systemctl() -> dict[str, str]:
    """User-session env for ``systemctl --user`` when the seat lacks XDG_RUNTIME_DIR."""
    env = os.environ.copy()
    if not os.environ.get("XDG_RUNTIME_DIR"):
        uid = os.getuid()
        xdg = f"/run/user/{uid}"
        env["XDG_RUNTIME_DIR"] = xdg
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={xdg}/bus"
    return env


def child_execute_argv(
    *,
    target_ref: str,
    tmux_target: str,
    result_path: Path,
    python_bin: str,
) -> list[str]:
    return [
        python_bin,
        "-m",
        "scripts.model_manager.guarded_manage_reexec",
        "--execute",
        "--recover",
        "--target-ref",
        target_ref,
        "--tmux-target",
        tmux_target,
        "--result-path",
        str(result_path),
    ]


def build_child_env(hub: Path) -> dict[str, str]:
    home = seat_operator_home()
    env: dict[str, str] = {
        "HOME": str(home),
        "PROJECT_ROOT": str(hub),
        "PYTHONPATH": f"{hub}/libs:{hub}/services/universal-stargate",
    }
    env.update(read_env_local_subset(hub))
    return env


def build_systemd_run_cmd(
    *,
    unit: str,
    hub: Path,
    child_argv: list[str],
    child_env: dict[str, str],
) -> list[str]:
    cmd: list[str] = [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit}",
        "-p",
        "KillMode=process",
        "-p",
        f"StandardOutput=append:{LOG_PATH}",
        "-p",
        "StandardError=inherit",
        f"--working-directory={hub}",
    ]
    for key, value in child_env.items():
        cmd.extend(["--setenv", f"{key}={value}"])
    cmd.append("--")
    cmd.extend(child_argv)
    return cmd


def launch_detach(
    *,
    target_ref: str,
    tmux_target: str,
    hub: Path = HUB_ROOT,
    result_path: Path = RESULT_PATH,
    run_cmd: RunCmd | None = None,
    python_bin: str | None = None,
) -> tuple[int, dict[str, object]]:
    """Admit one transient user service; return (exit_code, payload dict)."""
    run = run_cmd or _default_run
    python_bin = seat_python_bin(python_bin, default=str(seat_operator_home() / ".venvs/universal/bin/python"))
    launcher_env = launcher_env_for_systemctl()

    try:
        result_path.unlink(missing_ok=True)
    except OSError:
        pass

    list_proc = run(
        [
            "systemctl",
            "--user",
            "list-units",
            "--no-legend",
            "--plain",
            "--state=active,activating",
            f"{UNIT_PREFIX}*",
        ],
        env=launcher_env,
    )
    if list_proc.returncode != 0:
        return 2, {"status": "refused", "reason": "list_units_failed"}
    if parse_active_reexec_units(list_proc.stdout or ""):
        return 2, {"status": "refused", "reason": "reexec_unit_already_active"}

    unit = unit_name_for_launch()
    child_env = build_child_env(hub)
    child_argv = child_execute_argv(
        target_ref=target_ref,
        tmux_target=tmux_target,
        result_path=result_path,
        python_bin=python_bin,
    )

    systemd_argv = build_systemd_run_cmd(
        unit=unit,
        hub=hub,
        child_argv=child_argv,
        child_env=child_env,
    )
    proc = run(systemd_argv, env=launcher_env)
    if proc.returncode != 0:
        return 2, {
            "status": "refused",
            "reason": "systemd_run_failed",
            "stderr": (proc.stderr or "").strip(),
        }

    return 0, {
        "unit": unit,
        "result_path": str(result_path),
        "status": "launched",
    }


def default_manage_socket_from_hub(hub: Path) -> str:
    subset = read_env_local_subset(hub)
    return subset.get("MANAGE_SOCKET", MANAGE_SOCKET)


def _default_run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {"check": False, "text": True, "capture_output": True}
    if env is not None:
        kwargs["env"] = env
    return subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "HUB_ROOT",
    "LOG_PATH",
    "RESULT_PATH",
    "TMUX_BIN",
    "build_child_env",
    "build_systemd_run_cmd",
    "child_execute_argv",
    "launch_detach",
    "launcher_env_for_systemctl",
    "parse_active_reexec_units",
    "read_env_local_subset",
    "unit_name_for_launch",
]
