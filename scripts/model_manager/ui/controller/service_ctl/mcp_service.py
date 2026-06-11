"""MCP server lifecycle — canonical source sync + graceful restart.

Single deploy path for routine code changes: ``scripts/sync-and-restart-mcp.sh``
(docker cp into ``/app/`` + graceful restart, no image rebuild unless
``no_cache=True``).

Shared callers:
  - TUI Services → Sync + Start MCP
  - Fleet Sync + Restart All / Rebuild + Deploy All (via ``ServiceController.start_mcp``)
  - ``manage(action='start'|'restart'|'sync_restart', service='mcp')``
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..service_config import build_mcp_env, mcp_browser_override_path

logger = logging.getLogger(__name__)

_DETACHED_STDIN = asyncio.subprocess.DEVNULL


def mcp_compose_args(root: Path) -> tuple[list[str], Path] | None:
    """Return (docker compose args, compose_path) or None if missing."""
    compose_path = root / "docker" / "compose" / "mcp-server.yml"
    if not compose_path.exists():
        return None
    args = ["docker", "compose", "-f", str(compose_path)]
    override = mcp_browser_override_path(root)
    if override is not None and override.exists():
        args.extend(["-f", str(override)])
    return args, compose_path


async def sync_and_restart_mcp(root: Path, *, no_cache: bool = False) -> str:
    """Deploy latest workspace source into MCP and restart (canonical path).

    Runs ``scripts/sync-and-restart-mcp.sh``. Default: docker cp sync only.
    ``no_cache=True``: full image rebuild — pip/Dockerfile/base-image changes.
    """
    script = root / "scripts" / "sync-and-restart-mcp.sh"
    if not script.is_file():
        return f"Script not found: {script}"
    if mcp_compose_args(root) is None:
        return "Compose file not found: docker/compose/mcp-server.yml"
    env = build_mcp_env(root)
    cmd = ["bash", str(script)]
    if no_cache:
        cmd.append("--no-cache")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=_DETACHED_STDIN,
        env=env,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out = await proc.communicate()
    text = out[0].decode(errors="replace") if out[0] else ""
    if proc.returncode != 0:
        logger.error("MCP sync/restart failed (exit %d):\n%s", proc.returncode, text)
        return f"MCP sync/restart failed (exit {proc.returncode}).\n{text}"
    return f"MCP server synced and restarted.\n{text}"


async def stop_mcp(root: Path) -> str:
    """Stop and remove MCP server container."""
    base = mcp_compose_args(root)
    if base is None:
        return "MCP server is not running (compose file missing)."
    args, _ = base
    env = build_mcp_env(root)

    result = await asyncio.create_subprocess_exec(
        *args,
        "down",
        stdin=_DETACHED_STDIN,
        env=env,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = await result.communicate()
    text = output[0].decode(errors="replace") if output[0] else ""
    if result.returncode == 0:
        return f"MCP server stopped.\n{text}"
    logger.error("Failed to stop MCP server (exit %d):\n%s", result.returncode, text)
    return f"Failed to stop MCP server (exit {result.returncode}).\n{text}"
