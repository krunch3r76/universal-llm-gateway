"""
RAG source step handler — fetches full file content from RAG /source endpoint.

Host-local call: pipeline executor (Master Stargate on host) → RAG service
(UDS default or TCP via rag.host/rag.port in stargate.yaml).

Invariants:
- ∀ execute(): returns StepOutput.raw = reconstructed document text
- ∀ 404 (file not indexed): returns sentinel text (does not raise)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override
from urllib.parse import urlparse

from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from systems.pipeline.core.schemas import StepConfig
from transport_utils import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext

logger = get_logger(__name__)


def _resolve_rag_base_and_path(
    step: StepConfig, endpoint: str, default_path: str
) -> tuple[str, str]:
    """Resolve RAG base URL and path. Base: socket_path override or central config."""
    socket_path = step.get_domain_field("socket_path")
    if socket_path:
        base = (
            f"unix://{socket_path}"
            if not str(socket_path).startswith("unix://")
            else str(socket_path)
        )
    else:
        base = resolve_rag_base_url()
    path = urlparse(endpoint).path or default_path
    if not path.startswith("/"):
        path = f"/{path}"
    return base, path


class RagSourceHandler(AbstractStepHandler):
    """Fetch all chunks for a file from the RAG /source endpoint.

    Domain fields (from pipeline YAML step config):
        endpoint: str   — API path (e.g. /source); transport resolved via UDS at runtime.
        path: str       — file path to retrieve (required)
        socket_path: str — optional override for UDS path
    """

    step_type: str = "rag_source_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        endpoint: str = step.get_domain_field("endpoint", "")
        path: str = step.get_domain_field("path", "")

        if not endpoint:
            raise ValueError(f"Step '{step.id}': missing required 'endpoint' field")
        if not path:
            raise ValueError(f"Step '{step.id}': missing required 'path' field")

        base_url, api_path = _resolve_rag_base_and_path(step, endpoint, "/source")
        async with make_async_client(base_url, timeout=30.0) as client:
            response = await client.get(api_path, params={"path": path})

        if response.status_code == 404:
            logger.info("rag_source_v1 '%s': file not indexed: %s", step.id, path)
            return StepOutput(
                raw=f"File not indexed in knowledge base: {path}",
                json={"chunks_found": 0},
            )

        response.raise_for_status()
        data = response.json()
        chunks: list[str] = data.get("chunks", [])

        joined_text = "\n".join(chunks) if chunks else ""

        logger.debug(
            "rag_source_v1 '%s': path=%s, chunks_found=%d",
            step.id,
            path,
            len(chunks),
        )

        return StepOutput(
            raw=joined_text,
            json={"chunks_found": len(chunks)},
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.get_domain_field("endpoint"):
            errors.append(f"Step '{step.id}' missing required 'endpoint' field")
        if not step.get_domain_field("path"):
            errors.append(f"Step '{step.id}' missing required 'path' field")
        return errors
