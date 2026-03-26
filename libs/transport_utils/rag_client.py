"""RAG transport config helpers backed by ``stargate.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml

from transport_utils.client_factory import DEFAULT_RAG_URL

_STARGATE_CONFIG_PATH = Path.home() / ".gateway" / "stargate.yaml"


def resolve_rag_base_url() -> str:
    """Resolve RAG base URL from stargate.yaml (UDS default, TCP opt-in).

    Returns unix:///path or http://host:port. Used by pipeline handlers
    for runtime transport resolution.
    """
    if not _STARGATE_CONFIG_PATH.exists():
        return DEFAULT_RAG_URL
    try:
        data = yaml.safe_load(_STARGATE_CONFIG_PATH.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return DEFAULT_RAG_URL
    rag = data.get("rag")
    if not isinstance(rag, dict):
        return DEFAULT_RAG_URL
    host = rag.get("host")
    port_val = rag.get("port")
    if host is None or port_val is None:
        return DEFAULT_RAG_URL
    if not str(host).strip():
        return DEFAULT_RAG_URL
    try:
        port = int(port_val)
    except (TypeError, ValueError):
        return DEFAULT_RAG_URL
    if not (1 <= port <= 65535):
        return DEFAULT_RAG_URL
    return f"http://{host}:{port}"
