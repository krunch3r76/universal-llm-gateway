"""
RAG source step handler — fetches full file content from RAG /source endpoint.

Host-local call: pipeline executor (Master Stargate on host) → RAG service
(host port 8100). No container networking involved.

Invariants:
- ∀ execute(): returns StepOutput.raw = reconstructed document text
- ∀ 404 (file not indexed): returns sentinel text (does not raise)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import httpx
from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class RagSourceHandler(AbstractStepHandler):
    """Fetch all chunks for a file from the RAG /source endpoint.

    Domain fields (from pipeline YAML step config):
        endpoint: str   — RAG source URL (required, e.g. http://localhost:8100/source)
        path: str       — file path to retrieve (required)
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params={"path": path})

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
