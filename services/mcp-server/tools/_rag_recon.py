"""RAG recon aggregator — labeled per-theme search + durable sidecar persistence."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from durable_sink import ResolvedDurableSink, resolve_durable_sink
from mcp_events import monotonic_now, record
from provider_model_limits import rag_pipeline_timeout
from universal_logging import get_logger

logger = get_logger(__name__)

DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]


def _cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from ._cortex_relay import cx

    return cx("POST", "/dispatch", {"tool": tool, "arguments": json.dumps(arguments)})


def _run_theme_search(
    query: str,
    scopes: list[str],
    *,
    top_k: int,
) -> dict[str, Any]:
    from ._rag_retrieval_metadata import (
        envelope_retrieval_fields,
        retrieval_metadata_from_response,
    )
    from .rag import (
        _HTTP_BUFFER_S,
        _RERANK_MODEL_DEFAULT,
        _extract_content,
        _normalize_scope_override,
        _pipeline_call,
    )

    scope_override: str | list[str]
    if len(scopes) == 1:
        scope_override = scopes[0]
    else:
        scope_override = scopes
    normalized, scope_error = _normalize_scope_override(scope_override)
    if scope_error:
        return {"error": scope_error}

    pipeline_options: dict[str, Any] = {
        "scope_override": normalized,
        "rag_max_chunks": top_k,
        "include_retrieval_metadata": True,
    }
    rerank_model = pipeline_options.get("rerank_model", _RERANK_MODEL_DEFAULT)
    pipeline_timeout = rag_pipeline_timeout(rerank_model)
    pipeline_options["timeout_seconds"] = pipeline_timeout

    try:
        result = _pipeline_call(
            "rag-context",
            [{"role": "user", "content": query}],
            pipeline_options=pipeline_options,
            timeout=pipeline_timeout + _HTTP_BUFFER_S,
        )
    except Exception as exc:  # noqa: BLE001 — per-theme envelope
        return {"error": str(exc) or type(exc).__name__}

    content = _extract_content(result) if result else ""
    retrieval_fields = envelope_retrieval_fields(
        retrieval_metadata_from_response(result),
    )
    chunks_found = retrieval_fields.get("retrieval", {}).get("chunks_found", 0)
    return {
        "context": content,
        "content_length": len(content),
        "chunks_found": chunks_found,
        **retrieval_fields,
    }


def _relevance_tag(chunks_found: int, content_length: int) -> str:
    if chunks_found >= 3 or content_length >= 1200:
        return "RELEVANT"
    if chunks_found >= 1 or content_length >= 200:
        return "MARGINAL"
    return "SKIP"


def _build_theme_markdown(
    *,
    theme: str,
    scopes: list[str],
    queries: list[str],
    query_results: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Theme: {theme}",
        "",
        "## Scopes",
        *[f"- {scope}" for scope in scopes],
        "",
        "## Queries",
    ]
    for idx, query in enumerate(queries, start=1):
        lines.append(f"{idx}. {query}")
    lines.extend(["", "## Results", ""])
    for query, result in zip(queries, query_results, strict=False):
        if result.get("error"):
            lines.extend(
                [
                    f"### Query: {query}",
                    "",
                    f"_Search failed: {result['error']}_",
                    "",
                ]
            )
            continue
        tag = _relevance_tag(
            int(result.get("chunks_found") or 0),
            int(result.get("content_length") or 0),
        )
        lines.extend(
            [
                f"### Query: {query} [{tag}]",
                "",
                result.get("context") or "_No results._",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def execute_rag_recon(
    label: str,
    themes: list[dict[str, Any]],
    *,
    top_k: int = 20,
    durable_sink: str | None = None,
    dispatch_fn: DispatchFn | None = None,
    probe_fn: Callable[[], str] | None = None,
    resolve_sink_fn: Callable[..., ResolvedDurableSink] | None = None,
) -> dict[str, Any]:
    """Run labeled per-theme RAG recon and persist sidecars via DurableSink."""
    if not label.strip():
        return {"error": "label is required"}
    if not themes:
        return {"error": "themes is required"}

    t0 = monotonic_now()
    record("mcp.rag.recon.called", label=label, theme_count=len(themes))

    resolver = resolve_sink_fn or resolve_durable_sink
    try:
        resolved = resolver(
            backend_override=durable_sink,
            dispatch_fn=dispatch_fn or _cortex_dispatch,
            probe_fn=probe_fn,
        )
    except RuntimeError as exc:
        return {"error": str(exc)}

    theme_results: list[dict[str, Any]] = []
    evidence_uris: list[str] = []

    for raw_theme in themes:
        if not isinstance(raw_theme, dict):
            theme_results.append({"theme": None, "error": "invalid theme object"})
            continue
        theme_name = str(raw_theme.get("name") or raw_theme.get("theme") or "").strip()
        if not theme_name:
            theme_results.append({"theme": None, "error": "theme name is required"})
            continue
        scopes_raw = raw_theme.get("scopes") or []
        queries_raw = raw_theme.get("queries") or []
        if not isinstance(scopes_raw, list) or not isinstance(queries_raw, list):
            theme_results.append(
                {"theme": theme_name, "error": "scopes and queries must be lists"}
            )
            continue
        scopes = [str(s).strip() for s in scopes_raw if str(s).strip()]
        queries = [str(q).strip() for q in queries_raw if str(q).strip()]
        if not scopes or not queries:
            theme_results.append(
                {
                    "theme": theme_name,
                    "error": "each theme requires non-empty scopes and queries",
                }
            )
            continue

        query_results = [_run_theme_search(q, scopes, top_k=top_k) for q in queries]
        body = _build_theme_markdown(
            theme=theme_name,
            scopes=scopes,
            queries=queries,
            query_results=query_results,
        )
        total_chunks = sum(int(r.get("chunks_found") or 0) for r in query_results)
        entry: dict[str, Any] = {
            "theme": theme_name,
            "query_count": len(queries),
            "chunks_found": total_chunks,
        }
        try:
            sink_result = resolved.sink.write_recon_sidecar(
                label,
                theme_name,
                body,
                scopes=scopes,
                queries=queries,
                sink_backend=resolved.metadata.selected_backend,
            )
        except Exception as exc:  # noqa: BLE001 — per-theme failure
            entry["error"] = str(exc) or type(exc).__name__
            theme_results.append(entry)
            continue

        if sink_result is not None:
            entry["uri"] = sink_result.uri
            entry["sha256"] = sink_result.sha256
            entry["location"] = sink_result.location
            evidence_uris.append(sink_result.uri)
        theme_results.append(entry)

    duration = monotonic_now() - t0
    envelope: dict[str, Any] = {
        "status": "ok",
        "label": label,
        "themes": theme_results,
        "selected_backend": resolved.metadata.selected_backend,
        "selection_reason": resolved.metadata.selection_reason,
        "cortex_probe_status": resolved.metadata.cortex_probe_status,
        "fallback_used": resolved.metadata.fallback_used,
        "duration_s": round(duration, 3),
    }
    if evidence_uris:
        envelope["evidence_uris"] = evidence_uris
    if resolved.metadata.fallback_used:
        envelope["warning"] = (
            "Cortex durable sink unavailable; recon completed without cortex:// "
            "evidence URIs. Check selected_backend and fallback_used."
        )
    record(
        "mcp.rag.recon.completed",
        label=label,
        duration_s=round(duration, 3),
        backend=resolved.metadata.selected_backend,
        fallback=resolved.metadata.fallback_used,
    )
    return envelope
