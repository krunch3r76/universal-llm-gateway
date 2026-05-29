"""Per-dispatch HOME isolation for the cursorbuild runner.

cursor-agent reads login/auth + MCP config from ``~/.cursor/``. To run many
concurrent dispatches without them clobbering each other's session state — and
without risking the operator's real login — each dispatch gets a private HOME
whose ``.cursor/`` is seeded from the real one:

* ``cli-config.json`` (login/auth state) is **copied**, not symlinked. cursor
  rewrites this file via atomic rename on token refresh (validation B4); a
  symlink would either reintroduce that rename race across concurrent
  dispatches or, worse, let a dispatch corrupt the operator's real login. A
  copy gives each dispatch a private, stable credential snapshot.
* ``mcp.json`` is regenerated (not copied) containing ONLY the vortex MCP
  server entry, so a dispatch cannot reach unrelated MCP servers configured
  in the operator's real config.

The resulting homes are mutually disjoint: distinct dispatch ids yield
distinct directories, and mutating one never affects another or the real
``~/.cursor``. ``cursorbuild.argv._build_env`` overrides ``HOME`` to this path
when spawning the subprocess.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from universal_logging import get_logger

from cursorbuild.constants import (
    CURSOR_AUTH_FILENAME,
    CURSOR_CONFIG_DIRNAME,
    CURSOR_MCP_FILENAME,
)

logger = get_logger(__name__)


class CursorbuildConfigError(RuntimeError):
    """Raised when required cursor-agent config is missing for a dispatch.

    A dispatch that cannot be wired to a valid login (or, when MCP is
    requested, a valid vortex MCP server) must fail closed rather than spawn a
    subprocess that will hang on an interactive auth prompt or silently run
    without tooling.
    """


def dispatch_home_path(dispatch_id: str, sidecar_dir: Path) -> Path:
    """Return the per-dispatch HOME directory path (not yet created)."""
    return sidecar_dir / f"{dispatch_id}-home"


def _extract_vortex_mcp(real_mcp_path: Path) -> dict[str, object]:
    """Return the vortex-only ``mcpServers`` mapping from a real mcp.json.

    Filters the host ``mcpServers`` object down to entries whose key contains
    ``vortex`` (case-insensitive). Returns an empty dict when the file has no
    such entry; the caller decides whether that is fatal.
    """
    raw = json.loads(real_mcp_path.read_text(encoding="utf-8"))
    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {}
    return {name: cfg for name, cfg in servers.items() if "vortex" in name.lower()}


def setup_dispatch_home(
    dispatch_id: str,
    sidecar_dir: Path,
    *,
    real_home: str | None,
    mcp_enabled: bool,
) -> Path:
    """Create an isolated HOME for one dispatch and seed its ``.cursor/``.

    Copies the real ``cli-config.json`` (login state) into the dispatch's
    ``.cursor/`` and, when ``mcp_enabled``, writes a vortex-only ``mcp.json``.

    Returns the dispatch home path. Raises :class:`CursorbuildConfigError`
    when required config is absent:

    * ``mcp_enabled`` but no real ``cli-config.json`` (no login to copy).
    * ``mcp_enabled`` but no real ``mcp.json`` to derive the vortex entry from.
    * ``mcp_enabled`` but the real ``mcp.json`` defines no vortex server.

    Non-MCP dispatches degrade to a warning when login state is absent (the
    subprocess may still fail downstream, but that is the caller's call).

    Idempotent: re-running for the same id overwrites the seeded files.
    """
    home = dispatch_home_path(dispatch_id, sidecar_dir)
    cursor_dir = home / CURSOR_CONFIG_DIRNAME
    cursor_dir.mkdir(parents=True, exist_ok=True)

    real_cursor = Path(real_home) / CURSOR_CONFIG_DIRNAME if real_home else None

    # --- Login/auth: COPY (never symlink) cli-config.json. ---
    real_auth = real_cursor / CURSOR_AUTH_FILENAME if real_cursor else None
    if real_auth and real_auth.exists():
        shutil.copy2(real_auth, cursor_dir / CURSOR_AUTH_FILENAME)
        logger.debug(
            "dispatch_home[%s]: copied %s into %s",
            dispatch_id,
            CURSOR_AUTH_FILENAME,
            cursor_dir,
        )
    elif mcp_enabled:
        raise CursorbuildConfigError(
            f"dispatch[{dispatch_id}]: real {CURSOR_AUTH_FILENAME} not found "
            f"at {real_auth!r}; cannot run an MCP dispatch without login state"
        )
    else:
        logger.warning(
            "dispatch_home[%s]: real %s not found at %s; subprocess may lack "
            "cursor login state",
            dispatch_id,
            CURSOR_AUTH_FILENAME,
            real_auth,
        )

    # --- MCP: regenerate a vortex-only mcp.json. ---
    if mcp_enabled:
        real_mcp = real_cursor / CURSOR_MCP_FILENAME if real_cursor else None
        if not (real_mcp and real_mcp.exists()):
            raise CursorbuildConfigError(
                f"dispatch[{dispatch_id}]: real {CURSOR_MCP_FILENAME} not "
                f"found at {real_mcp!r}; cannot derive vortex MCP config"
            )
        vortex = _extract_vortex_mcp(real_mcp)
        if not vortex:
            raise CursorbuildConfigError(
                f"dispatch[{dispatch_id}]: no vortex server in real "
                f"{CURSOR_MCP_FILENAME} at {real_mcp!r}"
            )
        # OQ2 / Phase 4: the per-seat dispatch bearer token will be injected
        # into the vortex server's headers here once the seat-token plumbing
        # lands. For Phase 1 the copied host credentials are reused as-is.
        payload = json.dumps({"mcpServers": vortex}, indent=2) + "\n"
        (cursor_dir / CURSOR_MCP_FILENAME).write_text(payload, encoding="utf-8")
        logger.debug(
            "dispatch_home[%s]: wrote vortex-only %s (%d server(s))",
            dispatch_id,
            CURSOR_MCP_FILENAME,
            len(vortex),
        )

    return home
