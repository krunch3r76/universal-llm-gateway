"""
Synergize: merge N verified fact sets via embedding dedup.

No LLM call. Uses embedding cosine similarity to cluster duplicate
claims across independent verification chains, then picks the
first-occurrence representative (by source rank, then position).

Replaces the v4.0 enrich-reverify chain — eliminates rephrasing
attrition by operating directly on verified fact dicts.

Invariants:
    ∀ cluster: representative = argmin(-child_count, source_rank, input_index)
    ∀ fact ∈ output: fact ∈ some input verified_facts
    ∀ (i, j) ∈ cluster: cosine_sim(embed(i), embed(j)) >= threshold
    ∀ fact ∈ output: fact.parent_statement_id ∈ {f.statement_id | f ∈ output} ∨ fact.parent_statement_id = ∅
    |output| = |input| - |duplicates| - |orphans|
"""

from __future__ import annotations

import time
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
_DEFAULT_SIMILARITY_THRESHOLD = 0.82
# Metadata keys stamped during merge, stripped before output
_SOURCE_RANK = "_source_rank"
_INPUT_INDEX = "_input_index"


class SynergizeHandler(BaseHandler):
    """Merge N verified fact sets via embedding dedup. No LLM call."""

    step_type: str = "consensus_synergize_v5"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Merge verified fact lists, deduplicate by embedding similarity."""
        start_time = time.time()
        resolver = NamespaceResolver(context)

        # 1. Collect inputs — sorted key order determines source rank
        fact_lists, authority_dicts, all_rejected = self._collect_inputs(resolver, step)

        if not fact_lists:
            logger.error("Step '%s': no verified_facts inputs resolved", step.id)
            return StepOutput(
                raw="",
                json={"error": "no verified_facts inputs"},
                step_id=step.id,
            )

        # 2. Tag and concatenate
        all_facts: list[dict[str, Any]] = []
        input_counts: dict[str, int] = {}
        for rank, (source_name, facts) in enumerate(fact_lists):
            input_counts[source_name] = len(facts)
            for idx, fact in enumerate(facts):
                tagged = dict(fact)
                tagged[_SOURCE_RANK] = rank
                tagged[_INPUT_INDEX] = idx
                all_facts.append(tagged)

        total_input = len(all_facts)

        if total_input == 0:
            latency_ms = (time.time() - start_time) * 1000
            logger.warning("Step '%s': all fact lists empty", step.id)
            return StepOutput(
                raw="",
                json={
                    "verified_facts": [],
                    "rejected_claims": all_rejected,
                    "authority_verdicts": {},
                },
                step_id=step.id,
                latency_ms=latency_ms,
            )

        # 2b. Count children per statement_id across all facts.
        # Used to break cluster ties in favour of compound parents, preventing
        # a subclaim from evicting its own parent (filter_threshold appends
        # parents after their subclaims, so parents have higher _INPUT_INDEX
        # and would lose on input_index alone).
        child_counts: dict[str, int] = {}
        for fact in all_facts:
            pid = fact.get("parent_statement_id")
            if pid:
                child_counts[pid] = child_counts.get(pid, 0) + 1

        # 3. Embed and cluster
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

        texts = [get_statement_text(f) for f in all_facts]
        embeddings = await get_embeddings(
            texts, embedding_model, context, step_id=step.id
        )
        clusters, cluster_stats = cluster_similar(
            texts, embeddings, threshold, polarity_aware=True
        )

        # 4. Pick representative per cluster.
        # Priority: most children > lowest source_rank > lowest input_index.
        # ∀ cluster with a compound parent: parent wins over its own subclaims,
        # preventing the subclaim from evicting the parent that generated it.
        representatives: list[dict[str, Any]] = []
        for cluster_indices in clusters:
            cluster_facts = [all_facts[i] for i in cluster_indices]
            best = min(
                cluster_facts,
                key=lambda f: (
                    -child_counts.get(f.get("statement_id", ""), 0),
                    f[_SOURCE_RANK],
                    f[_INPUT_INDEX],
                ),
            )
            representatives.append(best)

        # 5. Sort output by (source_rank, input_index) for natural reading order
        representatives.sort(key=lambda f: (f[_SOURCE_RANK], f[_INPUT_INDEX]))

        # 6. Strip metadata tags
        for fact in representatives:
            fact.pop(_SOURCE_RANK, None)
            fact.pop(_INPUT_INDEX, None)

        # 6b. Orphan cleanup: remove subclaims whose parent was deduplicated out.
        # ∀ fact ∈ output: fact.parent_statement_id ∈ surviving_ids ∨ fact.parent_statement_id = ∅
        # When a parent loses a cluster to a claim from another link, its children
        # must not survive as dangling references.
        surviving_ids = {f.get("statement_id") for f in representatives}
        pre_orphan_count = len(representatives)
        representatives = [
            f
            for f in representatives
            if not f.get("parent_statement_id")
            or f["parent_statement_id"] in surviving_ids
        ]
        orphan_count = pre_orphan_count - len(representatives)
        if orphan_count:
            logger.warning(
                "Step '%s': removed %d orphan subclaim(s) whose parent was deduplicated",
                step.id,
                orphan_count,
            )

        # 7. Merge authority verdicts (first-seen key wins)
        merged_authority: dict[str, dict[str, Any]] = {}
        for _source_name, av_dict in authority_dicts:
            for claim_id, verdict in av_dict.items():
                if claim_id not in merged_authority:
                    merged_authority[claim_id] = verdict

        duplicate_count = total_input - pre_orphan_count
        latency_ms = (time.time() - start_time) * 1000

        logger.info(
            "Step '%s': synergized %d facts → %d unique "
            "(%d duplicates removed, %d orphan subclaims pruned) "
            "threshold=%.2f model=%s (%.0fms)",
            step.id,
            total_input,
            len(representatives),
            duplicate_count,
            orphan_count,
            threshold,
            embedding_model,
            latency_ms,
        )

        return StepOutput(
            raw="",
            json={
                "verified_facts": representatives,
                "rejected_claims": all_rejected,
                "authority_verdicts": merged_authority,
                "stats": {
                    "input_counts": input_counts,
                    "total_input": total_input,
                    "output_count": len(representatives),
                    "duplicate_count": duplicate_count,
                    "orphan_count": orphan_count,
                    "cluster_stats": cluster_stats,
                },
            },
            step_id=step.id,
            latency_ms=latency_ms,
        )

    def _collect_inputs(
        self,
        resolver: NamespaceResolver,
        step: StepConfig,
    ) -> tuple[
        list[tuple[str, list[dict[str, Any]]]],
        list[tuple[str, dict[str, dict[str, Any]]]],
        list[dict[str, Any]],
    ]:
        """Collect verified_facts_*, authority_verdicts_*, rejected_claims_* inputs.

        Returns:
            (fact_lists, authority_dicts, all_rejected) — fact_lists and
            authority_dicts ordered by key name; all_rejected is the
            concatenation of all rejected_claims_* lists (no dedup needed
            since it serves as a synthesis blacklist).
        """
        inputs = step.handler_inputs or {}
        fact_lists: list[tuple[str, list[dict[str, Any]]]] = []
        authority_dicts: list[tuple[str, dict[str, dict[str, Any]]]] = []
        all_rejected: list[dict[str, Any]] = []

        for key in sorted(inputs.keys()):
            resolved = self._resolve_input(resolver, step, key, inputs)
            if key.startswith("verified_facts"):
                facts = resolved if isinstance(resolved, list) else []
                fact_lists.append((key, facts))
            elif key.startswith("authority_verdicts"):
                av = resolved if isinstance(resolved, dict) else {}
                authority_dicts.append((key, av))
            elif key.startswith("rejected_claims"):
                rejected = resolved if isinstance(resolved, list) else []
                all_rejected.extend(rejected)

        return fact_lists, authority_dicts, all_rejected

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate synergize step configuration."""
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        fact_keys = [k for k in inputs if k.startswith("verified_facts")]
        if len(fact_keys) < 2:
            errors.append(
                f"Step '{step.id}': synergize requires at least 2 verified_facts_* inputs, "
                f"got {len(fact_keys)}"
            )
        return errors
