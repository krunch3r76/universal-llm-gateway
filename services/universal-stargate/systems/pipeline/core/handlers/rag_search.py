"""Built-in rag_search_v1: semantic search against the RAG service (UDS/TCP)."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

import httpx
from transport_utils import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

from ..execution.resolver import NamespaceResolver, traverse_path
from .protocol import PipelineContext, StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)


@register_handler
class RagSearchV1Handler:
    """POST /search on the RAG API; returns chunk texts + metadata as JSON."""

    step_type = "rag_search_v1"

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        resolved_map = getattr(step, "resolved_map_inputs", None) or {}
        query = resolved_map.get("query") or self._resolve_input(
            resolver, step, "query"
        )
        if isinstance(query, list):
            query = " ".join(str(t) for t in query if t is not None and str(t).strip())
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"Step '{step.id}': rag_search_v1 needs non-empty query")

        scope: str | list[str] | None = None
        if "scope" in resolved_map:
            scope = resolved_map["scope"]
        else:
            scope_field = step.handler_inputs.get("scope")
            if scope_field is not None:
                root = resolver.resolve(scope_field)
                scope = traverse_path(
                    root,
                    scope_field.field_path,
                    step_name=step.id,
                    field_name="scope",
                    binding_repr=str(scope_field),
                    resolver=resolver,
                )
        if isinstance(scope, str):
            pass
        elif isinstance(scope, list):
            scope = [str(s) for s in scope if s is not None and str(s).strip()]
        elif scope is not None:
            scope = str(scope)

        top_k = int(step.get_domain_field("top_k", 5) or 5)
        top_k = min(max(top_k, 1), 50)
        recency_weight = float(step.get_domain_field("recency_weight", 0.0) or 0.0)

        body: dict[str, Any] = {
            "query": query.strip(),
            "top_k": top_k,
            "recency_weight": recency_weight,
        }
        if scope is not None:
            body["scope"] = scope

        url = resolve_rag_base_url()
        try:
            async with make_async_client(url, timeout=120.0) as client:
                resp = await client.post("/search", json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "[%s] RAG /search returned error status: %s",
                step.id,
                e.response.status_code,
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                "[%s] RAG /search request failed: %s", step.id, e, exc_info=True
            )
            raise
        except Exception:
            logger.error(
                "[%s] RAG /search failed with unexpected error", step.id, exc_info=True
            )
            raise

        chunks = data.get("chunks") or []
        metadata = data.get("metadata") or []
        logger.debug(
            "[%s] rag_search ok: %d chunks (scope=%s)",
            step.id,
            len(chunks),
            scope,
        )
        payload = {
            "chunks": chunks,
            "metadata": metadata,
            "query": query.strip(),
            "scope": scope,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        return StepOutput(raw=raw, json=payload)

    def _resolve_input(
        self, resolver: NamespaceResolver, step: StepConfig, name: str
    ) -> Any:  # Consider a more specific type if possible, e.g., str | list[Any] | None
        binding = step.handler_inputs.get(name)
        if binding is None:
            raise ValueError(f"Step '{step.id}' missing handler_inputs.{name}")
        root = resolver.resolve(binding)
        return traverse_path(
            root,
            binding.field_path,
            step_name=step.id,
            field_name=name,
            binding_repr=str(binding),
            resolver=resolver,
        )
