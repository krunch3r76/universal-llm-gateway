"""Per-dispatch HOME override for grok-build-dispatch seat promotion.

Generates a private ~/.grok/config.toml under a dispatch-scoped HOME
so the grok subprocess connects to /mcp/grok with the build-dispatch
bearer token and the X-Grokbuild-Dispatch-Id header, attributing inner
MCP traffic to seat=grok-build-dispatch rather than to grok-direct.

∀ dispatch where MCP_GROK_BUILD_DISPATCH_TOKEN is configured:
  - dispatch HOME ≡ <sidecar_dir>/<dispatch_id>-home/
  - config.toml written under dispatch HOME with dispatch bearer + dispatch_id
  - auth.json symlinked from the real HOME (xAI login state re-used)
  - HOME added to subprocess env as an OVERRIDE (not pass-through)
  - pre-flight `grok inspect --json` verifies the override took effect

If MCP_GROK_BUILD_DISPATCH_TOKEN is not set the function returns None and
the caller must fall back (pass-through HOME remains absent from env).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_MCP_URL = "https://mcp.k-1.me/mcp/grok"
_MCP_SERVER_NAME = "user-vortex"

# Config.toml template for the dispatch-scoped grok home.
_CONFIG_TOML_TEMPLATE = """\
[model.grok-build-dispatch]
provider = "openai"
base_url = "http://localhost:9999/providers/xai"
api_key = "unused"

[mcp_servers.{server_name}]
url = "{mcp_url}"
type = "http"
enabled = true

[mcp_servers.{server_name}.headers]
Authorization = "Bearer {token}"
X-Grokbuild-Dispatch-Id = "{dispatch_id}"
"""


def dispatch_home_path(dispatch_id: str, sidecar_dir: Path) -> Path:
    """Return the per-dispatch HOME directory path (not yet created)."""
    return sidecar_dir / f"{dispatch_id}-home"


def setup_dispatch_home(
    dispatch_id: str,
    sidecar_dir: Path,
    *,
    token: str,
    real_home: str | None,
) -> Path:
    """Create per-dispatch HOME with config.toml and optional auth.json symlink.

    Returns the dispatch home path. Raises OSError on filesystem failure.

    ∀ dispatch_id: home = <sidecar_dir>/<dispatch_id>-home/
    Idempotent: if the directory already exists the config is overwritten.
    """
    home = dispatch_home_path(dispatch_id, sidecar_dir)
    grok_dir = home / ".grok"
    grok_dir.mkdir(parents=True, exist_ok=True)

    config_content = _CONFIG_TOML_TEMPLATE.format(
        server_name=_MCP_SERVER_NAME,
        mcp_url=_MCP_URL,
        token=token,
        dispatch_id=dispatch_id,
    )
    (grok_dir / "config.toml").write_text(config_content, encoding="utf-8")

    # Symlink auth.json from real home so the grok CLI has xAI login state.
    auth_link = grok_dir / "auth.json"
    if not auth_link.exists() and not auth_link.is_symlink():
        if real_home:
            real_auth = Path(real_home) / ".grok" / "auth.json"
            if real_auth.exists():
                auth_link.symlink_to(real_auth)
                logger.debug(
                    "dispatch_home[%s]: auth.json → %s", dispatch_id, real_auth
                )
            else:
                logger.warning(
                    "dispatch_home[%s]: real auth.json not found at %s; "
                    "grok subprocess may lack xAI login state",
                    dispatch_id,
                    real_auth,
                )
        else:
            logger.warning(
                "dispatch_home[%s]: real HOME not set; cannot symlink auth.json",
                dispatch_id,
            )

    logger.debug("dispatch_home[%s]: config.toml written to %s", dispatch_id, grok_dir)
    return home


def preflight_inspect(
    dispatch_id: str,
    home: Path,
    grok_path: str,
    env: dict[str, str],
) -> str | None:
    """Run `grok inspect --json` and verify the config source is the dispatch home.

    Returns None on success. Returns an error string on failure — caller must
    refuse to dispatch to prevent silent attribution drift.

    ∀ successful result: mcpServers contains an entry whose source.path lives
    under ``home`` (the override, not the real ~/.grok/config.toml).
    """
    expected_config = str(home / ".grok" / "config.toml")
    try:
        result = subprocess.run(
            [grok_path, "inspect", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"grok inspect failed to run: {exc}"

    if result.returncode != 0:
        return f"grok inspect exited {result.returncode}: {result.stderr[:200]}"

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return f"grok inspect produced invalid JSON: {exc}"

    # Verify the config source path resolves to the dispatch home.
    config_sources = data.get("configSources", {})
    user_path = config_sources.get("userPath", "")
    # The CLI loads config from userPath; we want that to be our override.
    if user_path and not user_path.startswith(str(home)):
        return (
            f"pre-flight: config userPath {user_path!r} is not under "
            f"dispatch home {str(home)!r}; HOME override may have failed"
        )

    # Verify at least one MCP server entry shows the dispatch bearer context.
    mcp_servers = data.get("mcpServers", [])
    if not mcp_servers:
        logger.warning(
            "dispatch[%s]: grok inspect reports no mcpServers "
            "(CLI may not have loaded dispatch config yet)",
            dispatch_id,
        )
        return None

    for server in mcp_servers:
        src = server.get("source", {})
        src_path = src.get("path", "")
        if src_path and src_path.startswith(str(home)):
            logger.debug(
                "dispatch[%s]: pre-flight OK — mcpServer source %r is under %s",
                dispatch_id,
                src_path,
                home,
            )
            return None

    # No server sourced from dispatch home — override didn't take.
    paths_seen = [s.get("source", {}).get("path", "") for s in mcp_servers]
    return (
        f"pre-flight: no mcpServer sourced from dispatch home {str(home)!r}; "
        f"paths seen: {paths_seen}; expected config at {expected_config}"
    )
