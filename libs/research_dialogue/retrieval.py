from __future__ import annotations

import uuid
from typing import Any, Callable, Protocol

import httpx
from transport_utils import DEFAULT_RAG_URL, make_sync_client

from research_dialogue._types import Passage, SearchResult

# Same scopes as C1-PLAN-r3 § r3.5 (Q6 default).
ALLOWLIST = frozenset(
    {
        "knowledge_systems",
        "agent_skills_research",
        "event_salience",
        "temporal_provenance",
        "belief_consistency",
        "llm_prompting",
        "claude_api",
        "prompt_injection",
        "all_research",
    }
)

PASSAGE_PREFIX = "\ue000"
MAX_PASSAGE_CHARS = 24_000
TOP_K = 10
PASSAGES_PER_QUERY = 5

# MCP rag_search uses Stargate passthrough POST api/v1/rag/search (rag.py:474).
# Direct RAG service exposes POST /search on DEFAULT_RAG_URL (UDS).
_RAG_SEARCH_PATH = "/search"


class _HttpClient(Protocol):
    def post(self, path: str, *, json: dict[str, Any]) -> httpx.Response: ...


def _mark_lines(text: str) -> str:
    return "\n".join(f"{PASSAGE_PREFIX}{line}" for line in text.splitlines())


def search(
    scope: str,
    query: str,
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> SearchResult:
    norm_scope = scope.strip()
    if norm_scope not in ALLOWLIST:
        return SearchResult(
            search_id="",
            scope=norm_scope,
            query=query,
            weak_match=None,
            rerank_status=None,
            top_ce=None,
            passages=[],
            dropped=True,
            drop_reason="not on allowlist",
        )

    def _default() -> _HttpClient:
        return make_sync_client(DEFAULT_RAG_URL, timeout=120.0)

    factory = client_factory or _default
    body = {"query": query.strip(), "top_k": TOP_K, "scope": norm_scope}
    search_id = f"rs-{uuid.uuid4().hex[:12]}"
    with factory() as client:
        resp = client.post(_RAG_SEARCH_PATH, json=body)
        resp.raise_for_status()
        payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError("rag search payload not object")

    chunks = payload.get("chunks") or []
    metadata = payload.get("metadata") or []
    distances = payload.get("distances") or []
    weak_match = payload.get("weak_match")
    rerank_status = payload.get("rerank_status")
    top_ce = payload.get("top_ce") or payload.get("top_relevance")
    if top_ce is None and distances:
        try:
            top_ce = 1.0 - float(distances[0])
        except (TypeError, ValueError):
            top_ce = None

    passages: list[Passage] = []
    total_chars = 0
    if isinstance(chunks, list):
        for idx, chunk in enumerate(chunks[:PASSAGES_PER_QUERY]):
            if not isinstance(chunk, str):
                continue
            md = metadata[idx] if isinstance(metadata, list) and idx < len(metadata) else {}
            source = ""
            if isinstance(md, dict):
                source = str(md.get("source") or md.get("path") or "")
            marked = _mark_lines(chunk)
            if total_chars + len(marked) > MAX_PASSAGE_CHARS:
                break
            total_chars += len(marked)
            passages.append(Passage(source=source, rank=idx + 1, text=marked))

    return SearchResult(
        search_id=search_id,
        scope=norm_scope,
        query=query,
        weak_match=weak_match if isinstance(weak_match, bool) else None,
        rerank_status=str(rerank_status) if rerank_status is not None else None,
        top_ce=float(top_ce) if top_ce is not None else None,
        passages=passages,
    )
