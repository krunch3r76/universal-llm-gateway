"""Stargate pipeline call helpers and the ``rag-context`` search execution body.

Who calls: ``tools/rag.py`` (``rag_search`` runs ``run_rag_search`` through the
in-flight registry in ``_rag_inflight``; ``rag_answer`` uses the call/error
helpers directly) and ``tools/_rag_recon.py`` (per-theme recon searches).

``run_rag_search`` is the unit of work the registry executes once per identical
request: it POSTs ``model=rag-context`` to Stargate ``/v1/chat/completions``,
records ``mcp.rag.pipeline.called`` / ``.completed`` / ``.failed``, and returns
the caller-facing envelope — ``status: ok`` with ``context`` + ``retrieval``
metadata, or ``{"error": ...}``. Transport failures also set ``retryable: true``
so the in-flight registry uses the failure TTL instead of caching them as a
600 s success. It never raises for transport failures; those become error
envelopes so every attached waiter receives the same answer.

Timeouts: the pipeline wall-clock comes from ``provider_model_limits
.rag_pipeline_timeout`` (model-load budget + reranker inference); the httpx
client allows ``HTTP_BUFFER_S`` more so Stargate's own timeout response arrives
before the client gives up.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from mcp_events import monotonic_now, record
from provider_model_limits import rag_pipeline_timeout
from transport_utils import make_sync_client

from ._rag_retrieval_metadata import (
    envelope_retrieval_fields,
    retrieval_metadata_from_response,
)

logger = logging.getLogger(__name__)

STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
# Default rerank model used by the rag-context pipeline; override via env when
# the pipeline is reconfigured to a different reranker.
RERANK_MODEL_DEFAULT = os.environ.get("RAG_RERANK_MODEL", "qwen3_9b")
# Extra seconds on the httpx client timeout beyond the pipeline wall-clock.
HTTP_BUFFER_S = 10.0

SCOPE_NOTE_CLASSIFIER = (
    "Auto-scope-classified search (no scope= given). ~68 scopes exist. "
    "Before concluding absence-of-evidence, call rag(op='list_scopes') and "
    "re-search with an explicit scope= over relevant domains."
)
SCOPE_NOTE_DEFAULT = (
    "Broad default-scope search (no scope= given). ~68 scopes exist. "
    "Before concluding absence-of-evidence, call rag(op='list_scopes') and "
    "re-search with an explicit scope= over relevant domains."
)
ZERO_RESULT_UNSCOPED_CAVEAT = (
    "Auto-scoped ≠ corpus-wide. Before concluding absence-of-evidence, "
    "call rag(op='list_scopes') and re-search with an explicit scope=."
)


def pipeline_call(
    model: str,
    messages: list[dict[str, Any]],
    *,
    pipeline_options: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    """POST a chat completion to Stargate and return the parsed JSON body.

    *model* names a pipeline (``rag-context``, ``rag-answer*``); *pipeline_options*
    ride in the request body. Raises the httpx error on transport or HTTP
    failure — callers map those through ``handle_pipeline_error``.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if pipeline_options:
        body["pipeline_options"] = pipeline_options

    url = "/v1/chat/completions"
    with make_sync_client(STARGATE_URL, timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


def _pipeline_transport_retryable(exc: BaseException) -> bool:
    """True for Stargate POST failures that must not sit in the success cache.

    Timeouts, connect/read failures, and HTTP 5xx are transient. A 4xx is the
    same request failing the same way and is left non-retryable.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.RequestError)


def handle_pipeline_error(
    exc: BaseException,
    pipeline: str,
    t0: float,
    user_message: str,
) -> dict[str, Any]:
    """Log, record ``mcp.rag.pipeline.failed``, and return the error envelope.

    Surfaces Stargate's own ``detail``/``error`` message for HTTP-status failures
    when present, otherwise *user_message*. Timeouts carry ``duration_s`` on the
    event so a wall-clock exhaustion is distinguishable from a connect failure.
    Transient transport failures set ``retryable: true`` so ``_retryable_failure``
    evicts them from the in-flight success cache.
    """
    extra: dict[str, Any] = {}
    surfaced_message = user_message
    if isinstance(exc, httpx.TimeoutException):
        duration = monotonic_now() - t0
        error_type = "timeout"
        log_message = f"Pipeline timed out after {duration:.1f}s: {exc}"
        extra["duration_s"] = round(duration, 3)
    elif isinstance(exc, httpx.ConnectError):
        error_type = str(exc)
        log_message = f"Stargate connection failed: {exc}"
    elif isinstance(exc, httpx.HTTPStatusError):
        error_type = f"{exc.response.status_code}"
        log_message = f"Pipeline HTTP error: {exc}"
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            nested = payload.get("detail", payload.get("error", {}))
            if isinstance(nested, dict) and nested.get("message"):
                surfaced_message = str(nested["message"])
            elif isinstance(nested, str) and nested.strip():
                surfaced_message = nested
    else:
        error_type = str(exc)
        log_message = f"Pipeline request error: {exc}"

    logger.warning(log_message, exc_info=True)
    record("mcp.rag.pipeline.failed", pipeline=pipeline, error=error_type, **extra)
    envelope: dict[str, Any] = {"error": surfaced_message}
    if _pipeline_transport_retryable(exc):
        envelope["retryable"] = True
    return envelope


def extract_content(response: dict[str, Any]) -> str:
    """Return the first choice's message content from an OpenAI-shaped completion,
    or ``""`` when the response carries no choices."""
    choices = response.get("choices", [])
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "")


def unscoped_scope_note(retrieval_fields: dict[str, Any]) -> str | None:
    """Return the scope advisory for an unscoped call, keyed on ``scope_source``
    (classifier vs default scope); ``None`` when an explicit override was used."""
    retrieval = retrieval_fields.get("retrieval", {})
    scope_source = retrieval.get("scope_source", "default_scope")
    if scope_source == "classifier":
        return SCOPE_NOTE_CLASSIFIER
    if scope_source == "default_scope":
        return SCOPE_NOTE_DEFAULT
    return None


def _catalog_unavailable(envelope: dict[str, Any]) -> bool:
    """True when the pipeline rejected the search because ``/scopes`` was down."""
    retrieval = envelope.get("retrieval")
    return (
        isinstance(retrieval, dict)
        and retrieval.get("scope_rejection_reason") == "scope_catalog_unavailable"
    )


def _catalog_transport_failure(envelope: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a catalog outage so it cannot be read as an empty corpus.

    Drops ``context`` (the no-results sentinel is non-empty and would otherwise
    take the ``status: ok`` path). ``retryable`` marks the envelope for the
    failure TTL in the in-flight cache.
    """
    retrieval = envelope.get("retrieval")
    failure: dict[str, Any] = {
        "status": "error",
        "retryable": True,
        "error": (
            "Scope catalog unavailable (RAG /scopes). "
            "This is a transport failure, not an empty corpus."
        ),
        "pipeline": envelope.get("pipeline", "rag-context"),
        "retrieval": retrieval if isinstance(retrieval, dict) else {},
    }
    if "duration_s" in envelope:
        failure["duration_s"] = envelope["duration_s"]
    return failure


def _retrieval_unavailable(envelope: dict[str, Any]) -> bool:
    """True when every retrieval query failed (RAG search transport down)."""
    retrieval = envelope.get("retrieval")
    return (
        isinstance(retrieval, dict)
        and retrieval.get("retrieval_rejection_reason") == "retrieval_unavailable"
    )


def _annotate_accepted_scope_empty(envelope: dict[str, Any]) -> dict[str, Any]:
    """Ensure ok+0 on an accepted scope carries ``empty_reason``.

    Transport rejections already have ``scope_rejection_reason`` /
    ``retrieval_rejection_reason``. A true index miss (or post-filter wipe)
    used to return only the no-results sentinel under ``status: ok``, which
    authors read as a null corpus. When the pipeline omitted a reason (older
    binary), synthesize ``unreported`` — not ``index_miss``, which claims a
    search ran.
    """
    retrieval = envelope.get("retrieval")
    if not isinstance(retrieval, dict):
        return envelope
    chunks_found = retrieval.get("chunks_found")
    if chunks_found != 0:
        return envelope
    if retrieval.get("empty_reason"):
        return envelope
    if retrieval.get("scope_rejection_reason") or retrieval.get(
        "retrieval_rejection_reason"
    ):
        return envelope
    if retrieval.get("scope_rejected"):
        return envelope
    annotated = dict(envelope)
    annotated_retrieval = dict(retrieval)
    annotated_retrieval["empty_reason"] = "unreported"
    annotated["retrieval"] = annotated_retrieval
    return annotated


def _retrieval_transport_failure(envelope: dict[str, Any]) -> dict[str, Any]:
    """Rewrite an all-queries-failed outage so it cannot be read as an empty corpus."""
    retrieval = envelope.get("retrieval")
    failure: dict[str, Any] = {
        "status": "error",
        "retryable": True,
        "error": (
            "Retrieval unavailable (RAG /search). "
            "This is a transport failure, not an empty corpus."
        ),
        "pipeline": envelope.get("pipeline", "rag-context"),
        "retrieval": retrieval if isinstance(retrieval, dict) else {},
    }
    if "duration_s" in envelope:
        failure["duration_s"] = envelope["duration_s"]
    return failure


def run_rag_search(
    query: str,
    *,
    scope: str | list[str] | None,
    prefixes: list[str] | None,
    pipeline_options: dict[str, Any],
    unscoped: bool,
) -> dict[str, Any]:
    """Execute one ``rag-context`` search and return its envelope.

    *pipeline_options* must already carry the normalized scope/prefix/chunk-cap
    options; the inner call adds ``timeout_seconds``. Returns ``status: ok``
    with ``context`` and ``retrieval`` on a real result, an ``error`` envelope
    when the pipeline produced no content, or ``handle_pipeline_error`` on
    transport failure. A ``scope_catalog_unavailable`` result is retried once;
    if it persists, the envelope is ``status: error`` and ``retryable`` with
    no ``context``, so a reader cannot take the outage for an empty corpus.
    Other scope rejections keep their existing sentinel behavior.
    """
    envelope = _rag_search_once(
        query,
        scope=scope,
        prefixes=prefixes,
        pipeline_options=pipeline_options,
        unscoped=unscoped,
    )
    if _retrieval_unavailable(envelope):
        return _retrieval_transport_failure(envelope)
    if not _catalog_unavailable(envelope):
        return envelope
    envelope = _rag_search_once(
        query,
        scope=scope,
        prefixes=prefixes,
        pipeline_options=dict(pipeline_options),
        unscoped=unscoped,
    )
    if _retrieval_unavailable(envelope):
        return _retrieval_transport_failure(envelope)
    if _catalog_unavailable(envelope):
        return _catalog_transport_failure(envelope)
    return envelope


def _rag_search_once(
    query: str,
    *,
    scope: str | list[str] | None,
    prefixes: list[str] | None,
    pipeline_options: dict[str, Any],
    unscoped: bool,
) -> dict[str, Any]:
    """One Stargate ``rag-context`` call, without the catalog-outage retry."""
    record_args: dict[str, Any] = {
        "pipeline": "rag-context",
        "query": query,
        "scope": scope,
    }
    if prefixes is not None:
        record_args["prefix"] = prefixes
    t0 = monotonic_now()
    record("mcp.rag.pipeline.called", **record_args)

    rerank_model = pipeline_options.get("rerank_model", RERANK_MODEL_DEFAULT)
    pipeline_timeout = rag_pipeline_timeout(rerank_model)
    pipeline_options["timeout_seconds"] = pipeline_timeout

    try:
        result = pipeline_call(
            "rag-context",
            [{"role": "user", "content": query}],
            pipeline_options=pipeline_options,
            timeout=pipeline_timeout + HTTP_BUFFER_S,
        )
    except httpx.TimeoutException as e:
        user_message = "Pipeline timed out. The query may be too complex."
        return handle_pipeline_error(e, "rag-context", t0, user_message)
    except httpx.ConnectError as e:
        user_message = "Pipeline not available. Stargate may not be running."
        return handle_pipeline_error(e, "rag-context", t0, user_message)
    except httpx.HTTPStatusError as e:
        user_message = (
            f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
        )
        return handle_pipeline_error(e, "rag-context", t0, user_message)
    except httpx.RequestError as e:
        user_message = f"Pipeline request failed: {e}"
        return handle_pipeline_error(e, "rag-context", t0, user_message)

    content = extract_content(result) if result else ""
    duration = monotonic_now() - t0
    retrieval_fields = envelope_retrieval_fields(
        retrieval_metadata_from_response(result),
    )
    scope_note = unscoped_scope_note(retrieval_fields) if unscoped else None

    if _retrieval_unavailable(retrieval_fields):
        record(
            "mcp.rag.pipeline.completed",
            pipeline="rag-context",
            duration_s=round(duration, 3),
            empty=True,
            query=query,
            scope=scope,
            prefix=prefixes,
            retrieval_unavailable=True,
        )
        return _retrieval_transport_failure(
            {
                "pipeline": "rag-context",
                "duration_s": round(duration, 3),
                **retrieval_fields,
            }
        )

    if not content:
        record(
            "mcp.rag.pipeline.completed",
            pipeline="rag-context",
            duration_s=round(duration, 3),
            empty=True,
            query=query,
            scope=scope,
            prefix=prefixes,
        )
        zero_note = ZERO_RESULT_UNSCOPED_CAVEAT if unscoped else None
        error = "Pipeline returned empty results."
        if zero_note:
            error = f"{error} {zero_note}"
        return {
            "error": error,
            **({"zero_result_caveat": zero_note} if zero_note else {}),
            **({"scope_note": scope_note} if scope_note else {}),
            **retrieval_fields,
        }

    logger.info(
        "rag_search: query=%r scope=%s prefix=%s → %d chars in %.1fs",
        query,
        scope,
        prefixes,
        len(content),
        duration,
    )
    record(
        "mcp.rag.pipeline.completed",
        pipeline="rag-context",
        duration_s=round(duration, 3),
        content_length=len(content),
        scope=scope,
        prefix=prefixes,
    )
    return _annotate_accepted_scope_empty(
        {
            "status": "ok",
            "pipeline": "rag-context",
            "content_length": len(content),
            "duration_s": round(duration, 3),
            "context": content,
            **({"scope_note": scope_note} if scope_note else {}),
            **retrieval_fields,
        }
    )
