"""Resolve the MCP /health URL for harvest proof-of-live probes.

Resolution order (first hit wins):
1. ``MCP_HEALTH_URL`` — explicit override for operators or tests.
2. ``MCP_PUBLIC_URL`` / ``MCP_SERVER_URL`` — derive ``{origin}/health`` by
   stripping MCP mount suffixes (``/mcp/code``, ``/mcp/life``, bare ``/mcp``).
3. ``~/.gateway/mcp.yaml`` ``mcp_server_url`` — same derivation; covers GIW
   and charter-runner processes that lack MCP env passthrough.
4. ``http://127.0.0.1:8080/health`` — local dev fallback only.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path
from urllib.parse import urlparse, urlunparse

_DISPATCH_HOME_MARKER = "cursor-dispatch-homes"
_LOCAL_DEFAULT = "http://127.0.0.1:8080/health"
_MCP_CONFIG = Path("~/.gateway/mcp.yaml")
_MOUNT_SUFFIXES = ("/mcp/code", "/mcp/life", "/mcp")


def _operator_home() -> Path:
    if op_home := os.environ.get("CHARTER_RUNNER_OPERATOR_HOME"):
        return Path(op_home).expanduser()
    current = Path.home()
    if _DISPATCH_HOME_MARKER in current.as_posix():
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return current


def _health_url_from_public_base(url: str) -> str:
    """Map an MCP public/server URL to the service-root ``/health`` endpoint."""
    stripped = url.strip().rstrip("/")
    if not stripped:
        return _LOCAL_DEFAULT
    parsed = urlparse(stripped)
    path = parsed.path.rstrip("/")
    for suffix in _MOUNT_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


def _mcp_server_url_from_yaml() -> str | None:
    path = _operator_home() / ".gateway" / "mcp.yaml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("mcp_server_url:"):
            continue
        value = stripped.split(":", 1)[1].strip().strip("'\"")
        return value or None
    return None


def resolve_mcp_health_probe_url() -> str:
    """Return the MCP health probe URL for propagation closure."""
    explicit = os.environ.get("MCP_HEALTH_URL", "").strip()
    if explicit:
        return explicit
    for env_name in ("MCP_PUBLIC_URL", "MCP_SERVER_URL"):
        public = os.environ.get(env_name, "").strip()
        if public:
            return _health_url_from_public_base(public)
    yaml_url = _mcp_server_url_from_yaml()
    if yaml_url:
        return _health_url_from_public_base(yaml_url)
    return _LOCAL_DEFAULT


__all__ = ["resolve_mcp_health_probe_url"]
