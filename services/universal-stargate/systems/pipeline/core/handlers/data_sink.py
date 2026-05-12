"""Built-in data_sink_v1: persist pipeline outputs to RAG metadata (SQLite)."""
# ruff: noqa: E501

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from universal_logging import get_logger

from ..execution.resolver import NamespaceResolver, traverse_path
from .protocol import PipelineContext, StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)


@register_handler
class DataSinkV1Handler:
    """Write structured results via PropertyIndex (scope_vocabulary + freshness)."""

    step_type = "data_sink_v1"
    dependency_fields: ClassVar[tuple[str, ...]] = ()

    def validate(self, step: StepConfig) -> list[str]:
        st = step.get_domain_field("sink_type", "")
        if st != "scope_vocabulary":
            return [
                f"Step '{step.id}': data_sink_v1 requires sink_type and must be 'scope_vocabulary', got {st!r}"
            ]
        return []

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        sink_type = step.get_domain_field("sink_type", "")
        if sink_type != "scope_vocabulary":
            raise ValueError(f"Step '{step.id}': unsupported sink_type {sink_type!r}")

        resolver = NamespaceResolver(context)
        vocabulary = self._resolve_input(resolver, step, "vocabulary")
        if not isinstance(vocabulary, dict):
            raise ValueError(
                f"Step '{step.id}': handler_inputs.vocabulary must resolve to a dict"
            )
        vocabulary: dict[str, dict] = vocabulary  # Narrow type after check

        tier_raw = str(context.options.get("mode") or "local").strip().lower()
        tier = tier_raw if tier_raw in ("local", "frontier") else "local"

        scope_hashes = self._optional_input(resolver, step, "scope_hashes")
        if scope_hashes is not None and not isinstance(scope_hashes, dict):
            raise ValueError(
                f"Step '{step.id}': scope_hashes must be a dict[str, str] when set"
            )

        all_scopes_raw = self._optional_input(resolver, step, "all_scopes")
        failed_scopes: list[str] = []
        if isinstance(all_scopes_raw, list):
            input_scope_names = {
                str(s.get("scope") or "").strip()
                for s in all_scopes_raw
                if isinstance(s, dict)
            } - {""}
            failed_scopes = sorted(input_scope_names - set(vocabulary.keys()))

        from services.rag.property_index import PropertyIndex

        idx = PropertyIndex()
        await idx.start()
        try:
            if vocabulary:
                await idx.replace_scope_vocabulary_for_scopes(vocabulary)
                for scope_name, registers in vocabulary.items():
                    if not isinstance(scope_name, str) or not scope_name.strip():
                        continue
                    if not isinstance(registers, dict):
                        continue
                    h = None
                    if isinstance(scope_hashes, dict):
                        raw_h = scope_hashes.get(scope_name)
                        if isinstance(raw_h, str) and raw_h.strip():
                            h = raw_h.strip()
                    if not h:
                        logger.error(
                            "[%s] Missing files_hash for scope %r; "
                            "skipping store_scope_freshness",
                            step.id,
                            scope_name,
                        )
                        continue
                    await idx.store_scope_freshness(scope_name, h, classified_tier=tier)
                await idx.stamp_watermark("vocabulary")
                await idx.stamp_watermark("corpus_hints")

            for scope in failed_scopes:
                await idx.invalidate_scope_freshness(scope)
                logger.warning(
                    "[%s] Scope %r failed — freshness invalidated",
                    step.id,
                    scope,
                )
        finally:
            await idx.stop()

        summary: dict[str, object] = {
            "written_scopes": sorted(vocabulary.keys()),
            "classified_tier": tier,
        }
        if failed_scopes:
            summary["invalidated_scopes"] = failed_scopes
        raw = json.dumps(summary, ensure_ascii=False)
        return StepOutput(raw=raw, json=summary)

    def _get_input(
        self, resolver: NamespaceResolver, step: StepConfig, name: str, optional: bool
    ) -> Any:
        binding = step.handler_inputs.get(name)
        if binding is None:
            if optional:
                return None
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

    def _resolve_input(
        self, resolver: NamespaceResolver, step: StepConfig, name: str
    ) -> Any:
        return self._get_input(resolver, step, name, optional=False)

    def _optional_input(
        self, resolver: NamespaceResolver, step: StepConfig, name: str
    ) -> Any:
        return self._get_input(resolver, step, name, optional=True)
