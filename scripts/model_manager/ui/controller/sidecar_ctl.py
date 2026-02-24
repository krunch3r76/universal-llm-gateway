"""Sidecar controller - start, stop, and inspect the pipeline-tools container."""

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_NAME = "pipeline-tools"
SIDECAR_IMAGE = "stargate-tools:latest"


class SidecarController:
    """Manages the pipeline-tools sidecar container lifecycle.

    The sidecar is a warm, hardened Alpine container (network-isolated,
    read-only FS, non-root, capability-dropped) used by pipeline tool
    handlers to execute deterministic steps (shell commands, git, etc.).

    Security posture:
        --network none, --read-only, --user 1000:1000,
        --cap-drop ALL, --security-opt no-new-privileges,
        --pids-limit 64, --memory 256m, --cpus 1.0

    INV: workspace mount path = workspace_root (never hardcoded)
    """

    def __init__(self, workspace_root: Path) -> None:
        self._root: Path = workspace_root

    async def start(self) -> str:
        """Start the pipeline-tools sidecar container."""
        if self.running():
            return f"Sidecar '{SIDECAR_NAME}' is already running."

        result = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "-d",
            "--name",
            SIDECAR_NAME,
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1.0",
            "--tmpfs",
            "/tmp",
            "-v",
            f"{self._root}:/workspace:ro",
            SIDECAR_IMAGE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await result.communicate()
        text = output[0].decode(errors="replace").strip() if output[0] else ""
        if result.returncode == 0:
            return f"Sidecar '{SIDECAR_NAME}' started."
        return f"Failed to start sidecar (exit {result.returncode}).\n{text}"

    async def stop(self) -> str:
        """Stop and remove the pipeline-tools sidecar container."""
        if not self.running():
            return f"Sidecar '{SIDECAR_NAME}' is not running."

        stop = await asyncio.create_subprocess_exec(
            "docker",
            "stop",
            SIDECAR_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _ = await stop.communicate()

        rm = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            SIDECAR_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _ = await rm.communicate()
        return f"Sidecar '{SIDECAR_NAME}' stopped and removed."

    def running(self) -> bool:
        """Check if the pipeline-tools sidecar container is running."""
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    SIDECAR_NAME,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() == "true"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
