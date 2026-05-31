"""RAG service utilities: socket detection, scope discovery, retrieval."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
from transport_utils import DEFAULT_RAG_URL, make_sync_client

from .constants import DEFAULT_STARGATE_URL


def rag_socket_present(rag_url: str) -> bool:
    """Return True if the RAG socket file exists (unix://) or URL is TCP.

    Existence check only — not a full connectivity probe. A stale socket
    (process crashed, file left behind) returns True here; the subsequent
    fetch call will surface a ConnectError and continue gracefully. The
    purpose is to suppress the noisy "No such file or directory" message
    that fires when RAG is simply not configured/started.

    For TCP URLs: always returns True (errors surface at connection time).
    Default unix socket path when not specified: /tmp/universal-protocol/rag.sock.
    """
    if not rag_url.startswith("unix://"):
        return True
    rest = rag_url[7:].lstrip("/")
    socket_path = (
        Path(f"/{rest}")
        if rest
        else Path(os.environ.get("RAG_SOCKET_PATH", "/tmp/universal-protocol/rag.sock"))
    )
    return socket_path.exists()


def fetch_scope_choices(rag_url: str = DEFAULT_RAG_URL) -> list[str]:
    """Fetch available scopes from RAG service for CLI choices."""
    try:
        with make_sync_client(rag_url, timeout=2.0) as client:
            resp = client.get("/scopes")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            scopes = data.get("scopes")
            if isinstance(scopes, dict) and scopes:
                return list(scopes.keys())
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        print(
            f"RAG scope discovery failed ({type(exc).__name__}); using defaults",
            file=sys.stderr,
        )
    return ["project", "research", "all"]


def fetch_scope_options_text(rag_url: str = DEFAULT_RAG_URL) -> str:
    """Fetch and format scopes for rag-context prompt injection."""
    try:
        with make_sync_client(rag_url, timeout=2.0) as client:
            resp = client.get("/scopes")
        resp.raise_for_status()
        data = resp.json()
        scopes = data.get("scopes") if isinstance(data, dict) else None
        if not isinstance(scopes, dict) or not scopes:
            return '"research", "project", or "all"'
        lines: list[str] = []
        for name, info in scopes.items():
            if name == "all":
                continue
            desc = info.get("description") if isinstance(info, dict) else None
            desc_str = desc if isinstance(desc, str) else ""
            lines.append(f'"{name}" — {desc_str}' if desc_str else f'"{name}"')
        lines.append('"all" — when unclear or mixed across multiple scopes')
        return "\n        ".join(lines)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        print(
            f"RAG scope option fetch failed ({type(exc).__name__}); using fallback text",
            file=sys.stderr,
        )
        return '"research", "project", or "all"'


def fetch_rag_pipeline(
    query: str,
    *,
    pipeline_id: str = "rag-context",
    stargate_url: str = DEFAULT_STARGATE_URL,
    rag_url: str = DEFAULT_RAG_URL,
    timeout: float = 70.0,
    scope_override: str | list[str] | None = None,
    extra_pipeline_options: dict[str, Any] | None = None,
) -> tuple[list[str], str | None]:
    """Use the rag-context pipeline for intelligent RAG retrieval."""
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    pipeline_options: dict[str, Any] = {
        "scope_options": fetch_scope_options_text(rag_url=rag_url),
    }
    if scope_override is not None:
        pipeline_options["scope_override"] = (
            scope_override  # str or list[str]; pipeline accepts both
        )
    if extra_pipeline_options:
        pipeline_options.update(extra_pipeline_options)
    body: dict[str, Any] = {
        "model": pipeline_id,
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "pipeline_options": pipeline_options,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            return [], f"pipeline.step.error: {exc}"
        return [], str(exc)
    except httpx.RequestError as exc:
        return [], f"Request failed: {exc}"
    except Exception as exc:
        import logging

        logging.getLogger(__name__).exception(
            "Unexpected error during fetch_rag_pipeline"
        )
        return [], str(exc)
    data = resp.json()
    content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content.strip():
        return [], f"Pipeline '{pipeline_id}' returned empty context"
    return [content], None


def fetch_rag_direct(
    query: str,
    *,
    rag_url: str = DEFAULT_RAG_URL,
    top_k: int = 5,
    scope: str | list[str] | None = None,
    timeout: float = 10.0,
) -> tuple[list[str], str | None]:
    """Direct RAG search via the /search endpoint. scope may be a single name or list (union)."""
    body: dict[str, object] = {"query": query, "top_k": top_k}
    if scope:
        body["scope"] = scope  # RAG accepts str or list[str]
    try:
        with make_sync_client(rag_url, timeout=timeout) as client:
            resp = client.post("/search", json=body)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        return [], f"RAG service unreachable ({type(exc).__name__})"
    data = resp.json()
    if not isinstance(data, dict):
        return [], "RAG response was not a JSON object"
    chunks = data.get("chunks", [])
    metadata = data.get("metadata", [])
    findings: list[str] = []
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, str):
            continue
        meta_item = metadata[idx] if idx < len(metadata) else None
        meta = meta_item if isinstance(meta_item, dict) else {}
        source = str(meta.get("source", "unknown"))
        findings.append(f"Source: {source}\n{chunk}")
    return findings, None
