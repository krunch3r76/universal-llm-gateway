"""
Group facts by embedding similarity.

No LLM call. Reorders verified_facts so topically similar facts are adjacent,
using complete-linkage hierarchical clustering on L2-normalized embeddings.

Preserves all fact dicts unmodified — only their order changes.

Invariants:
    ∀ fact ∈ output: fact ∈ input verified_facts
    |output| == |input|
    ∀ (i, j) in same cluster: cosine_sim(embed(i), embed(j)) >= threshold
    ∀ cluster: no (i, j) with polarity_conflict(i, j)  [polarity_aware=True]
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._dedup_clustering import cluster_similar
from .shared._dedup_embeddings import get_embeddings
from .shared._text_utils import get_statement_text

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_EMBEDDING_MODEL = "embedding"
_DEFAULT_SIMILARITY_THRESHOLD = 0.75
_DEBUG_OUT = Path("/tmp/group_facts.out")


def _write_debug(
    reordered: list[dict[str, Any]],
    clusters: list[set[int]],
    original: list[dict[str, Any]],
) -> None:
    """Write reordered facts to /tmp/group_facts.out for debugging.

    Each fact is rendered as one line per source_sentences index:
        [n] <fact text>
    Cluster boundaries are marked with a blank line and a header.
    """
    # Map original index → cluster number for boundary annotation.
    idx_to_cluster: dict[int, int] = {}
    for cluster_num, cluster in enumerate(clusters):
        for idx in cluster:
            idx_to_cluster[idx] = cluster_num

    original_text_to_idx: dict[str, int] = {
        get_statement_text(f): i for i, f in enumerate(original)
    }

    lines: list[str] = []
    current_cluster: int | None = None

    for fact in reordered:
        text = get_statement_text(fact)
        orig_idx = original_text_to_idx.get(text)
        cluster_num = idx_to_cluster.get(orig_idx, -1) if orig_idx is not None else -1

        if cluster_num != current_cluster:
            if lines:
                lines.append("")
            size = len(clusters[cluster_num]) if 0 <= cluster_num < len(clusters) else 0
            lines.append(
                f"--- cluster {cluster_num} ({size} fact{'s' if size != 1 else ''}) ---"
            )
            current_cluster = cluster_num

        source_sentences: list[int] = fact.get("source_sentences") or []
        prefix = (
            f"[{','.join(str(n) for n in source_sentences)}]"
            if source_sentences
            else "[-]"
        )
        lines.append(f"{prefix} {text}")

    try:
        _DEBUG_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning(
            "group_facts: could not write debug output to %s: %s", _DEBUG_OUT, e
        )


class GroupFactsHandler(BaseHandler):
    """Reorder verified_facts so topically similar facts are adjacent. No LLM call."""

    step_type: str = "consensus_group_facts_v7"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Embed fact texts, cluster by similarity, flatten clusters into reordered list."""
        start_time = time.time()
        resolver = NamespaceResolver(context)

        verified_facts: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "verified_facts", step.handler_inputs or {}
        )
        if not isinstance(verified_facts, list):
            verified_facts = []

        if not verified_facts:
            logger.warning("Step '%s': verified_facts is empty", step.id)
            return StepOutput(
                raw="",
                json={
                    "verified_facts": [],
                    "stats": {"input_count": 0, "group_count": 0, "singleton_count": 0},
                },
                step_id=step.id,
                latency_ms=(time.time() - start_time) * 1000,
            )

        embedding_model = str(
            step.get_domain_field("embedding_model") or _DEFAULT_EMBEDDING_MODEL
        )
        threshold = step.get_domain_field("similarity_threshold")
        if threshold is None:
            threshold = _DEFAULT_SIMILARITY_THRESHOLD
            logger.error(
                "Step '%s': similarity_threshold not configured, using default %.2f",
                step.id,
                threshold,
            )
        threshold = float(threshold)

        # Texts are extracted in input order — positional correspondence with
        # verified_facts is the only provenance available (upstream veto returns
        # a plain list with no index markers).
        texts = [get_statement_text(f) for f in verified_facts]

        embeddings = await get_embeddings(
            texts, embedding_model, context, step_id=step.id
        )
        clusters, cluster_stats = cluster_similar(
            texts, embeddings, threshold, polarity_aware=True
        )

        # Flatten clusters into a reordered flat list.
        # Walk clusters sequentially; within each cluster emit facts in ascending
        # original-index order (sorted set → stable, reproducible output).
        reordered: list[dict[str, Any]] = []
        for cluster in clusters:
            for idx in sorted(cluster):
                reordered.append(verified_facts[idx])

        singleton_count = sum(1 for c in clusters if len(c) == 1)
        latency_ms = (time.time() - start_time) * 1000

        _write_debug(reordered, clusters, verified_facts)

        logger.info(
            "Step '%s': grouped %d facts into %d clusters "
            "(%d singletons) threshold=%.2f model=%s (%.0fms)",
            step.id,
            len(verified_facts),
            len(clusters),
            singleton_count,
            threshold,
            embedding_model,
            latency_ms,
        )

        fact_clusters: list[list[dict[str, Any]]] = [
            [verified_facts[idx] for idx in sorted(cluster)]
            for cluster in clusters
        ]

        return StepOutput(
            raw="",
            json={
                "verified_facts": reordered,
                "fact_clusters": fact_clusters,
                "stats": {
                    "input_count": len(verified_facts),
                    "group_count": len(clusters),
                    "singleton_count": singleton_count,
                    "cluster_stats": cluster_stats,
                },
            },
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate group_facts step configuration."""
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        if "verified_facts" not in inputs:
            errors.append(
                f"Step '{step.id}': consensus_group_facts_v7 requires 'verified_facts' in handler_inputs"
            )
        return errors
