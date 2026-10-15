"""Credential attestation strings for agent-bus turn rows."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

_PEER_NODES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "mcp" / "peer_nodes.yaml"
)


@functools.cache
def _peer_node_map() -> dict[str, str]:
    import yaml

    if not _PEER_NODES_PATH.is_file():
        return {}
    raw = yaml.safe_load(_PEER_NODES_PATH.read_text(encoding="utf-8")) or {}
    peers = raw.get("peers") if isinstance(raw, dict) else None
    if not isinstance(peers, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in peers.items() if k and v}


def resolve_peer_node(client_ip: str | None) -> tuple[str, str]:
    """Map observed client IP to (node_name, ip) for attestation; unknown → unknown(<ip>)."""
    ip = (client_ip or "").strip() or "unknown"
    node = _peer_node_map().get(ip, "unknown")
    return node, ip


def classify_client_runtime(user_agent: str | None) -> str | None:
    """Classify MCP client runtime from server-observed User-Agent."""
    ua = (user_agent or "").strip().lower()
    if not ua:
        return None
    if "undici" in ua:
        return "grok-bot"
    if "claude-user" in ua:
        return "claude-web"
    if "python-httpx" in ua or "httpx" in ua:
        return "ulg-local"
    if "cursor" in ua or "mozilla" in ua:
        return "cursor-ide"
    return None


def attest(
    auth_mode: object,
    caller_identity: object,
    surface: object,
    *,
    client_ip: object = None,
    user_agent: object = None,
) -> str | None:
    """Return a stable attestation label when auth context is complete.

    The value names the MCP credential triple (mode, identity, mount surface),
    optionally suffixed with server-observed via/client stamps.
    """
    if not isinstance(auth_mode, str) or not auth_mode.strip():
        return None
    if not isinstance(caller_identity, str) or not caller_identity.strip():
        return None
    if not isinstance(surface, str) or not surface.strip():
        return None
    base = f"{auth_mode.strip()}:{caller_identity.strip()}@{surface.strip()}"
    parts: list[str] = [base]
    if isinstance(client_ip, str) and client_ip.strip():
        node, ip = resolve_peer_node(client_ip)
        parts.append(f"via={node}({ip})")
    runtime = classify_client_runtime(user_agent if isinstance(user_agent, str) else None)
    if runtime:
        parts.append(f"client={runtime}")
    return " ".join(parts)


def reset_peer_node_cache_for_tests() -> None:
    """Test isolation — drop cached peer_nodes.yaml."""
    _peer_node_map.cache_clear()
