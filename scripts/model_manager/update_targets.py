"""Remote target discovery for `./manage update`."""

from pathlib import Path
from urllib.parse import urlparse

import yaml

_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_MASTER_CONFIG = _GATEWAY_DIR / "stargate.yaml"


def collect_remote_targets(target_host: str | None) -> list[tuple[str, str]]:
    """Collect (hostname, ssh_user) for remotes to update."""
    remotes = _list_remotes()
    if not remotes:
        return []

    out: list[tuple[str, str]] = []
    for remote in remotes:
        hostname = _hostname_from_remote(remote)
        if not hostname:
            continue
        if target_host and hostname != target_host:
            continue

        node_env = _NODES_DIR / f"{hostname}.env"
        if not node_env.exists():
            print(f"WARNING: Node env not found: {node_env}, skipping {hostname}")
            continue

        ssh_user = _read_node_env_key(node_env, "SSH_USER")
        if not ssh_user:
            print(f"WARNING: SSH_USER missing in {node_env}, skipping {hostname}")
            continue

        out.append((hostname, ssh_user))

    if target_host and not out and remotes:
        if not any(_hostname_from_remote(r) == target_host for r in remotes):
            print(f"ERROR: Remote '{target_host}' not found in federation config.")
        else:
            print(f"ERROR: Remote '{target_host}' missing node env or SSH_USER.")
    return out


def _list_remotes() -> list[dict[str, str]]:
    """Read remote nodes from ~/.gateway/stargate.yaml."""
    if not _MASTER_CONFIG.exists():
        return []
    data = yaml.safe_load(_MASTER_CONFIG.read_text()) or {}
    return data.get("federation", {}).get("remotes") or []


def _read_node_env_key(node_env: Path, key: str) -> str | None:
    """Read a single KEY=value from a node env file."""
    prefix = f"{key}="
    for line in node_env.read_text().splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def _hostname_from_remote(remote: dict[str, str]) -> str:
    """Extract hostname from remote URL (e.g. 'http://jupiter:9999' -> 'jupiter')."""
    url = remote.get("url", "")
    parsed = urlparse(url)
    return parsed.hostname or ""
