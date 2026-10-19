"""Remote target discovery for ``./manage update``.

Resolves ``(hostname, ssh_user)`` targets from configured federation remotes and
per-node env files under ``~/.gateway/nodes``. Supports optional single-host
filtering and logs actionable reasons when a configured remote is not deployable.
"""

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml
from transport_utils.gateway_dir import resolve_gateway_dir

logger = logging.getLogger(__name__)

_GATEWAY_DIR = resolve_gateway_dir(env_var=None)
_NODES_DIR = _GATEWAY_DIR / "nodes"
_MASTER_CONFIG = _GATEWAY_DIR / "stargate.yaml"


def collect_remote_targets(target_host: str | None) -> list[tuple[str, str]]:
    """Collect (hostname, ssh_user) for remotes to update."""
    remotes = _list_remotes()
    if not remotes:
        return []
    if target_host:
        remotes = [r for r in remotes if target_host in _remote_names(r)]
        if not remotes:
            logger.error("Remote '%s' not found in federation config.", target_host)
            return []

    out: list[tuple[str, str]] = []
    for remote in remotes:
        resolved = _resolve_node_env(remote)
        if resolved is None:
            logger.warning(
                "Node env not found for %s, skipping",
                remote.get("stargate_id") or remote.get("url"),
            )
            continue
        node_env, ssh_host = resolved

        ssh_user = _read_node_env_key(node_env, "SSH_USER")
        if not ssh_user:
            logger.warning("SSH_USER missing in %s, skipping %s", node_env, ssh_host)
            continue

        out.append((ssh_host, ssh_user))

    if target_host and not out:
        logger.error("Remote '%s' missing node env or SSH_USER.", target_host)
    return out


def _list_remotes() -> list[dict[str, str]]:
    """Read remote nodes from ~/.gateway/stargate.yaml."""
    if not _MASTER_CONFIG.exists():
        return []
    try:
        data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    except yaml.YAMLError as e:
        logger.error("Malformed stargate config %s: %s", _MASTER_CONFIG, e)
        return []
    return data.get("federation", {}).get("remotes") or []


def _read_node_env_key(node_env: Path, key: str) -> str | None:
    """Read a single KEY=value from a node env file."""
    m = re.search(rf"^{re.escape(key)}=(.+)$", node_env.read_text(), re.MULTILINE)
    return m.group(1) if m else None


def _remote_names(remote: dict[str, str]) -> set[str]:
    """Names that identify a remote: URL host and ``relay-`` stripped id."""
    names: set[str] = set()
    host = _hostname_from_remote(remote)
    if host:
        names.add(host)
    sid = str(remote.get("stargate_id", ""))
    if sid.startswith("relay-"):
        names.add(sid.removeprefix("relay-"))
    return names


def _resolve_node_env(remote: dict[str, str]) -> tuple[Path, str] | None:
    """Return ``(env path, ssh host)`` for a federation remote.

    Node env files are named for the node (``jupiter.env``). The federation URL
    is often an IP, so try the ``relay-`` id before the URL host. The matching
    name is the SSH host (``jupiter``, not ``10.0.0.76``).
    """
    ordered: list[str] = []
    sid = str(remote.get("stargate_id", ""))
    if sid.startswith("relay-"):
        ordered.append(sid.removeprefix("relay-"))
    host = _hostname_from_remote(remote)
    if host and host not in ordered:
        ordered.append(host)
    for name in ordered:
        path = _NODES_DIR / f"{name}.env"
        if path.is_file():
            return path, name
    return None


def _hostname_from_remote(remote: dict[str, str]) -> str | None:
    """Extract hostname from remote URL (e.g. 'http://jupiter:9999' -> 'jupiter')."""
    url = remote.get("url", "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    # parsed.hostname is None for bare-scheme URLs like 'http://'; fall back to
    # netloc which still contains the host for non-standard URLs.
    host = (parsed.hostname or parsed.netloc or "").strip()
    return host or None
