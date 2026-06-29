"""Host GPU prerequisites for docker compose edge/gateway starts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_PERSISTENCED_SOCKET = Path("/run/nvidia-persistenced/socket")
_TUI_WARNING_TITLE = "GPU host check — Gateway / Sync+Restart blocked until fixed"


def check_gpu_docker_prerequisites() -> str | None:
    """Return an actionable error when the host cannot start GPU containers."""
    if not shutil.which("docker"):
        return "Docker is not installed or not on PATH."

    persistenced_err = _check_nvidia_persistenced()
    if persistenced_err is not None:
        return persistenced_err

    driver_err = _check_nvidia_driver()
    if driver_err is not None:
        return driver_err

    return None


def format_gpu_warning_for_tui(message: str) -> str:
    """Rich markup for the home-screen GPU warning banner."""
    body = "\n".join(f"  {line}" for line in message.strip().splitlines())
    return f"[bold red]⚠ {_TUI_WARNING_TITLE}[/]\n{body}"


def _check_nvidia_persistenced() -> str | None:
    if _PERSISTENCED_SOCKET.exists():
        return None

    active = _systemd_unit_active("nvidia-persistenced")
    if active is True:
        return (
            "NVIDIA persistence daemon is active but "
            f"{_PERSISTENCED_SOCKET} is missing.\n"
            "Try: sudo systemctl restart nvidia-persistenced"
        )
    if active is False:
        return (
            "NVIDIA persistence daemon is not running "
            f"({_PERSISTENCED_SOCKET} missing).\n"
            "Fix: sudo systemctl start nvidia-persistenced\n"
            "Then retry Sync + Restart All."
        )
    return (
        f"GPU container runtime requires {_PERSISTENCED_SOCKET}.\n"
        "Fix: sudo systemctl start nvidia-persistenced"
    )


def _check_nvidia_driver() -> str | None:
    if not shutil.which("nvidia-smi"):
        return (
            "nvidia-smi not found — install NVIDIA drivers and "
            "NVIDIA Container Toolkit, then retry."
        )

    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("nvidia-smi probe failed: %s", exc)
        return f"Could not query GPU state: {exc}"

    if result.returncode == 0:
        return None

    combined = f"{result.stdout}\n{result.stderr}".strip()
    if "driver/library version mismatch" in combined.lower():
        return (
            "NVIDIA driver/library version mismatch "
            "(kernel module vs userspace libraries).\n"
            "Fix: reboot the host so the loaded driver matches the installed "
            "packages, then retry Sync + Restart All."
        )

    tail = combined[-400:] if combined else "no output"
    return f"nvidia-smi failed (exit {result.returncode}).\n{tail}"


def _systemd_unit_active(unit: str) -> bool | None:
    if not shutil.which("systemctl"):
        return None
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = result.stdout.strip()
    if state == "active":
        return True
    if state in {"inactive", "failed"}:
        return False
    return None
