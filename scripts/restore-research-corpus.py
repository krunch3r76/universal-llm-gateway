#!/usr/bin/env python3
"""
Restore docs/research/ corpus by downloading PDFs from arXiv.

All arXiv IDs are verified — either from the embedded arXiv ID in the PDF
itself, or via targeted web search. Papers not on arXiv are listed in
PENDING_PAPERS below and are intentionally skipped.

Usage:
    python scripts/restore-research-corpus.py           # download all
    python scripts/restore-research-corpus.py --dry-run # preview only
"""

import asyncio
import sys
from pathlib import Path

import httpx

RESEARCH_ROOT = Path("/mnt/torus/projects/universal-llm-gateway/docs/research")

# ---------------------------------------------------------------------------
# PENDING — papers NOT on arXiv; must be sourced manually.
# ---------------------------------------------------------------------------
# These files are intentionally skipped. The slug and reason are documented
# so they can be found and downloaded by hand if needed.
#
# slug                                   source / reason
# ─────────────────────────────────────  ──────────────────────────────────
# rag-systems/cormack-rrf-2009.pdf       SIGIR 2009 conference paper (no arXiv)
#                                        PDF: https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
# temporal-provenance/acl2020-provenance-claims.pdf
#                                        ACL 2020, no arXiv preprint
#                                        ACL Anthology: 2020.acl-main.406
# temporal-provenance/acl2021-fine-grained-provenance.pdf
#                                        ACL 2021, no arXiv preprint
#                                        ACL Anthology: 2021.acl-long.458
# temporal-provenance/green2007-provenance-semirings.pdf
#                                        PODS 2007, no arXiv preprint
#                                        Authors: Todd J. Green, Grigoris Karvounarakis, Val Tannen
# temporal-provenance/provsql-semiring-provenance-postgresql.pdf
#                                        VLDB 2018, available on HAL (hal-01851538)
#                                        Authors: Aryak Sen, Silviu Maniu, Pierre Senellart
# ---------------------------------------------------------------------------

# (arXiv ID, target_dir, filename)
ARXIV_PAPERS: list[tuple[str, str, str]] = [
    # -----------------------------------------------------------------------
    # rag-systems (59 files — all covered except cormack-rrf-2009, see PENDING)
    # -----------------------------------------------------------------------
    ("2312.10997", "rag-systems", "gao-rag-survey-2024.pdf"),
    ("2407.01219", "rag-systems", "searching-best-practices-rag.pdf"),
    ("2506.00054", "rag-systems", "rag-architecture-survey-2025.pdf"),
    ("2601.05264", "rag-systems", "engineering-rag-stack-2025.pdf"),
    ("2504.19754", "rag-systems", "advanced-chunking-strategies-rag.pdf"),
    ("2406.00456", "rag-systems", "mix-of-granularity-chunking.pdf"),
    ("2312.06648", "rag-systems", "dense-x-retrieval-propositions.pdf"),
    ("2409.04701", "rag-systems", "late-chunking-jina.pdf"),
    ("2401.18059", "rag-systems", "raptor-tree-organized-retrieval.pdf"),
    ("2404.07220", "rag-systems", "blended-rag-hybrid.pdf"),
    ("2402.03367", "rag-systems", "rag-fusion-rrf.pdf"),
    ("2506.15862", "rag-systems", "mixture-of-retrievers.pdf"),
    ("2408.11119", "rag-systems", "mistral-splade.pdf"),
    ("2403.06789", "rag-systems", "splade-v3.pdf"),
    ("2402.03216", "rag-systems", "bge-m3-embedding.pdf"),
    ("2402.01613", "rag-systems", "nomic-embed.pdf"),
    ("2210.07316", "rag-systems", "mteb-benchmark.pdf"),
    ("2408.16672", "rag-systems", "jina-colbert-v2.pdf"),
    ("2407.02485", "rag-systems", "rankrag-context-ranking.pdf"),
    ("2310.11511", "rag-systems", "self-rag.pdf"),
    ("2406.19215", "rag-systems", "seakr-adaptive-rag.pdf"),
    ("2310.04408", "rag-systems", "recomp-context-compression.pdf"),
    ("2405.13792", "rag-systems", "xrag-extreme-compression.pdf"),
    ("2309.15217", "rag-systems", "ragas-evaluation.pdf"),
    ("2311.09476", "rag-systems", "ares-evaluation-framework.pdf"),
    ("2504.17137", "rag-systems", "mirage-rag-benchmark.pdf"),
    ("2409.11242", "rag-systems", "trust-align-rag.pdf"),
    ("2404.12457", "rag-systems", "ragcache-knowledge-caching.pdf"),
    ("2405.16444", "rag-systems", "cacheblend-kv-fusion.pdf"),
    ("2502.20969", "rag-systems", "telerag-lookahead-retrieval.pdf"),
    ("2412.21023", "rag-systems", "edgerag-edge-devices.pdf"),
    ("2502.10993", "rag-systems", "roserag-small-llms.pdf"),
    ("2406.15319", "rag-systems", "longrag-long-context.pdf"),
    ("2512.03413", "rag-systems", "bookrag-hierarchical-indexing.pdf"),
    ("2503.10150", "rag-systems", "hirag-hierarchical-knowledge.pdf"),
    ("2501.09136", "rag-systems", "agentic-rag-survey-2025.pdf"),
    ("2112.01488", "rag-systems", "colbertv2.pdf"),
    (
        "2311.07914",
        "rag-systems",
        "llm-agents-rag-knowledge-graphs-reflections-2025.pdf",
    ),
    ("2212.10509", "rag-systems", "ircot-interleaving-retrieval-cot.pdf"),
    ("2502.18397", "rag-systems", "kirag-iterative-retrieval.pdf"),
    ("2510.18633", "rag-systems", "query-decomp-exploration-exploitation.pdf"),
    ("2504.09554", "rag-systems", "mixture-of-rag-text-tables.pdf"),
    ("2407.16833", "rag-systems", "rag-vs-long-context.pdf"),
    # Reranking papers (not in original script — IDs from embedded arXiv headers)
    ("2304.09542", "rag-systems", "rankgpt-listwise-reranking.pdf"),
    ("2309.15088", "rag-systems", "rankvicuna-open-source-listwise.pdf"),
    ("2312.02724", "rag-systems", "rankzephyr-zero-shot-listwise.pdf"),
    ("2310.09497", "rag-systems", "setwise-reranking-prompting.pdf"),
    ("2310.07712", "rag-systems", "permutation-self-consistency-ranking.pdf"),
    ("2406.15657", "rag-systems", "first-faster-listwise-single-token.pdf"),
    ("2405.07920", "rag-systems", "rank-distillm-cross-encoder-distillation.pdf"),
    ("2411.04602", "rag-systems", "scalr-self-calibrated-listwise.pdf"),
    ("2412.14574", "rag-systems", "sliding-window-vs-full-ranking.pdf"),
    # Cross-encoder vs LLM: "A Thorough Comparison of Cross-Encoders and LLMs for Reranking SPLADE"
    # Previous wrong download was RAG-Star (2412.12881) — corrected
    ("2403.10407", "rag-systems", "cross-encoder-vs-llm-reranking.pdf"),
    # Other rag-systems (IDs from embedded arXiv headers)
    ("2406.13663", "rag-systems", "mirage-answer-attribution.pdf"),
    ("2408.14698", "rag-systems", "balancing-blend-hybrid-search.pdf"),
    ("2402.07483", "rag-systems", "rag-from-pdfs-experience.pdf"),
    ("2412.04661", "rag-systems", "heal-domain-embedding.pdf"),
    ("2507.00355", "rag-systems", "question-decomposition-rag.pdf"),
    # -----------------------------------------------------------------------
    # small_llm/prompting (59 files — prompt engineering for small/local models)
    # -----------------------------------------------------------------------
    ("2307.03172", "small_llm/prompting", "lost-in-the-middle.pdf"),
    ("2211.01910", "small_llm/prompting", "human-level-prompt-engineers.pdf"),
    ("2201.11903", "small_llm/prompting", "chain-of-thought-prompting.pdf"),
    ("2311.05661", "small_llm/prompting", "prompt-engineering-a-prompt-engineer.pdf"),
    ("2406.06608", "small_llm/prompting", "prompt-report-survey.pdf"),
    ("2203.11171", "small_llm/prompting", "self-consistency.pdf"),
    ("2305.10601", "small_llm/prompting", "tree-of-thoughts.pdf"),
    ("2305.20050", "small_llm/prompting", "lets-verify-step-by-step.pdf"),
    ("2310.03714", "small_llm/prompting", "dspy-compiling-lm-pipelines.pdf"),
    ("2401.14295", "small_llm/prompting", "demystifying-chains-trees-graphs.pdf"),
    ("2402.07927", "small_llm/prompting", "systematic-survey-prompt-engineering.pdf"),
    ("2406.11695", "small_llm/prompting", "mipro-multi-stage-optimization.pdf"),
    ("2510.05077", "small_llm/prompting", "slm-mux-orchestrating-small-lms.pdf"),
    ("2305.14325", "small_llm/prompting", "multiagent-debate-factuality.pdf"),
    ("2309.13007", "small_llm/prompting", "reconcile-roundtable-consensus.pdf"),
    ("2412.01928", "small_llm/prompting", "malt-multi-agent-llm-training.pdf"),
    ("2402.05120", "small_llm/prompting", "more-agents-is-all-you-need.pdf"),
    ("2404.17140", "small_llm/prompting", "small-lms-need-strong-verifiers.pdf"),
    ("2406.04692", "small_llm/prompting", "mixture-of-agents.pdf"),
    ("2404.14618", "small_llm/prompting", "hybrid-llm-query-routing.pdf"),
    ("2402.01620", "small_llm/prompting", "magdi-multi-agent-distillation.pdf"),
    ("2601.07245", "small_llm/prompting", "multi-model-consensus-reasoning-engine.pdf"),
    (
        "2512.20184",
        "small_llm/prompting",
        "reaching-agreement-reasoning-llm-agents.pdf",
    ),
    ("2503.10881", "small_llm/prompting", "scalable-consistency-ensembles.pdf"),
    ("2502.15631", "small_llm/prompting", "o3-thinks-harder-not-longer.pdf"),
    (
        "2509.13196",
        "small_llm/prompting",
        "few-shot-dilemma-over-prompting-large-llms.pdf",
    ),
    (
        "2602.08672",
        "small_llm/prompting",
        "llm-designing-applying-evaluation-rubrics.pdf",
    ),
    ("2512.16041", "small_llm/prompting", "assessing-llm-as-a-judge.pdf"),
    ("2503.23989", "small_llm/prompting", "rubric-is-all-you-need-code-evaluation.pdf"),
    ("2502.02988", "small_llm/prompting", "themis-fine-tuned-llm-judge.pdf"),
    ("2501.10868", "small_llm/prompting", "json-schema-bench-structured-outputs.pdf"),
    ("2505.20139", "small_llm/prompting", "struct-eval-llm-structural-outputs.pdf"),
    ("2512.01420", "small_llm/prompting", "prompt-bridge-cross-model-transfer.pdf"),
    ("2504.16005", "small_llm/prompting", "capo-cost-aware-prompt-optimization.pdf"),
    ("2508.02053", "small_llm/prompting", "procut-attribution-prompt-compression.pdf"),
    ("2511.12281", "small_llm/prompting", "cmprsr-abstractive-prompt-compression.pdf"),
    ("2502.16923", "small_llm/prompting", "survey-automatic-prompt-optimization.pdf"),
    (
        "2505.16307",
        "small_llm/prompting",
        "pmpo-probabilistic-metric-prompt-optimization.pdf",
    ),
    (
        "2511.19829",
        "small_llm/prompting",
        "evaluation-instructed-prompt-optimization.pdf",
    ),
    (
        "2504.10179",
        "small_llm/prompting",
        "adaptive-mllm-prompting-comprehensive-evaluation.pdf",
    ),
    ("2508.11126", "small_llm/prompting", "ai-agentic-programming-survey.pdf"),
    ("2509.19376", "small_llm/prompting", "solving-freshness-rag-recency-prior.pdf"),
    (
        "2412.15540",
        "small_llm/prompting",
        "mrag-modular-retrieval-time-sensitive-qa.pdf",
    ),
    (
        "2502.21024",
        "small_llm/prompting",
        "tempretriever-temporal-dense-passage-retrieval.pdf",
    ),
    ("2507.22917", "small_llm/prompting", "ta-rag-diachronic-question-answering.pdf"),
    ("2509.11353", "small_llm/prompting", "recency-bias-llm-reranking.pdf"),
    ("2403.14403", "small_llm/prompting", "adaptive-rag-question-complexity.pdf"),
    (
        "2401.15884",
        "small_llm/prompting",
        "crag-corrective-retrieval-augmented-generation.pdf",
    ),
    (
        "2305.06983",
        "small_llm/prompting",
        "flare-active-retrieval-augmented-generation.pdf",
    ),
    ("2212.10496", "small_llm/prompting", "hyde-hypothetical-document-embeddings.pdf"),
    ("2401.05856", "small_llm/prompting", "seven-failure-points-rag.pdf"),
    ("2310.06117", "small_llm/prompting", "step-back-prompting-abstraction.pdf"),
    ("2410.02185", "small_llm/prompting", "posix-prompt-sensitivity-index.pdf"),
    ("2401.06766", "small_llm/prompting", "template-ensembles-prompt-robustness.pdf"),
    (
        "2504.03975",
        "small_llm/prompting",
        "greater-prompt-optimization-small-models.pdf",
    ),
    ("2311.10227", "small_llm/prompting", "persona-prompting-not-helpful.pdf"),
    # Corrected IDs (previous downloads had wrong arXiv IDs)
    # "Deciphering the Factors Influencing the Efficacy of CoT: Probability, Memorization, Noisy Reasoning"
    # Previous wrong download: "Is GPT-3 a Good Data Annotator?" (2212.10450)
    ("2407.01687", "small_llm/prompting", "cot-factors-probability-memorization.pdf"),
    # "Quantifying Language Models' Sensitivity to Spurious Features in Prompt Design" (Sclar et al., ICLR 2024)
    # Previous wrong download: "Multi-Step Reasoning with LLMs: a Survey" (2407.11511)
    ("2310.11324", "small_llm/prompting", "format-spread-prompt-sensitivity.pdf"),
    # "Revisiting OPRO: The Limitations of Small-Scale LLMs as Optimizers" (ACL Findings 2024)
    # Previous wrong download: "Semantic API Alignment" (2405.04236)
    ("2405.10276", "small_llm/prompting", "revisiting-opro-small-llm-optimizers.pdf"),
    # -----------------------------------------------------------------------
    # llm/prompting (6 files — prompt engineering for large/cloud models)
    # -----------------------------------------------------------------------
    ("2501.10868", "llm/prompting", "json-schema-bench-structured-outputs.pdf"),
    ("2501.04682", "llm/prompting", "meta-chain-of-thought-system2-reasoning.pdf"),
    ("2410.14826", "llm/prompting", "sprig-system-prompt-optimization.pdf"),
    ("2505.05315", "llm/prompting", "elastic-reasoning-scalable-cot.pdf"),
    ("2512.02840", "llm/prompting", "promptolution-modular-prompt-optimization.pdf"),
    ("2601.06403", "llm/prompting", "steer-model-system-prompt-adherence.pdf"),
    # -----------------------------------------------------------------------
    # prompting/ — general prompting research (tier-agnostic superset)
    # Same papers as small_llm/prompting + llm/prompting with all fixes applied.
    # -----------------------------------------------------------------------
    ("2307.03172", "prompting", "lost-in-the-middle.pdf"),
    ("2211.01910", "prompting", "human-level-prompt-engineers.pdf"),
    ("2201.11903", "prompting", "chain-of-thought-prompting.pdf"),
    ("2311.05661", "prompting", "prompt-engineering-a-prompt-engineer.pdf"),
    ("2406.06608", "prompting", "prompt-report-survey.pdf"),
    ("2203.11171", "prompting", "self-consistency.pdf"),
    ("2305.10601", "prompting", "tree-of-thoughts.pdf"),
    ("2305.20050", "prompting", "lets-verify-step-by-step.pdf"),
    ("2310.03714", "prompting", "dspy-compiling-lm-pipelines.pdf"),
    ("2401.14295", "prompting", "demystifying-chains-trees-graphs.pdf"),
    ("2402.07927", "prompting", "systematic-survey-prompt-engineering.pdf"),
    ("2406.11695", "prompting", "mipro-multi-stage-optimization.pdf"),
    ("2510.05077", "prompting", "slm-mux-orchestrating-small-lms.pdf"),
    ("2305.14325", "prompting", "multiagent-debate-factuality.pdf"),
    ("2309.13007", "prompting", "reconcile-roundtable-consensus.pdf"),
    ("2412.01928", "prompting", "malt-multi-agent-llm-training.pdf"),
    ("2402.05120", "prompting", "more-agents-is-all-you-need.pdf"),
    ("2404.17140", "prompting", "small-lms-need-strong-verifiers.pdf"),
    ("2406.04692", "prompting", "mixture-of-agents.pdf"),
    ("2404.14618", "prompting", "hybrid-llm-query-routing.pdf"),
    ("2402.01620", "prompting", "magdi-multi-agent-distillation.pdf"),
    ("2601.07245", "prompting", "multi-model-consensus-reasoning-engine.pdf"),
    ("2512.20184", "prompting", "reaching-agreement-reasoning-llm-agents.pdf"),
    ("2503.10881", "prompting", "scalable-consistency-ensembles.pdf"),
    ("2502.15631", "prompting", "o3-thinks-harder-not-longer.pdf"),
    ("2509.13196", "prompting", "few-shot-dilemma-over-prompting-large-llms.pdf"),
    ("2602.08672", "prompting", "llm-designing-applying-evaluation-rubrics.pdf"),
    ("2512.16041", "prompting", "assessing-llm-as-a-judge.pdf"),
    ("2503.23989", "prompting", "rubric-is-all-you-need-code-evaluation.pdf"),
    ("2502.02988", "prompting", "themis-fine-tuned-llm-judge.pdf"),
    ("2501.10868", "prompting", "json-schema-bench-structured-outputs.pdf"),
    ("2505.20139", "prompting", "struct-eval-llm-structural-outputs.pdf"),
    ("2512.01420", "prompting", "prompt-bridge-cross-model-transfer.pdf"),
    ("2504.16005", "prompting", "capo-cost-aware-prompt-optimization.pdf"),
    ("2508.02053", "prompting", "procut-attribution-prompt-compression.pdf"),
    ("2511.12281", "prompting", "cmprsr-abstractive-prompt-compression.pdf"),
    ("2502.16923", "prompting", "survey-automatic-prompt-optimization.pdf"),
    ("2505.16307", "prompting", "pmpo-probabilistic-metric-prompt-optimization.pdf"),
    ("2511.19829", "prompting", "evaluation-instructed-prompt-optimization.pdf"),
    ("2504.10179", "prompting", "adaptive-mllm-prompting-comprehensive-evaluation.pdf"),
    ("2508.11126", "prompting", "ai-agentic-programming-survey.pdf"),
    ("2509.19376", "prompting", "solving-freshness-rag-recency-prior.pdf"),
    ("2412.15540", "prompting", "mrag-modular-retrieval-time-sensitive-qa.pdf"),
    ("2502.21024", "prompting", "tempretriever-temporal-dense-passage-retrieval.pdf"),
    ("2507.22917", "prompting", "ta-rag-diachronic-question-answering.pdf"),
    ("2509.11353", "prompting", "recency-bias-llm-reranking.pdf"),
    ("2403.14403", "prompting", "adaptive-rag-question-complexity.pdf"),
    ("2401.15884", "prompting", "crag-corrective-retrieval-augmented-generation.pdf"),
    ("2305.06983", "prompting", "flare-active-retrieval-augmented-generation.pdf"),
    ("2212.10496", "prompting", "hyde-hypothetical-document-embeddings.pdf"),
    ("2401.05856", "prompting", "seven-failure-points-rag.pdf"),
    ("2310.06117", "prompting", "step-back-prompting-abstraction.pdf"),
    ("2410.02185", "prompting", "posix-prompt-sensitivity-index.pdf"),
    ("2401.06766", "prompting", "template-ensembles-prompt-robustness.pdf"),
    ("2504.03975", "prompting", "greater-prompt-optimization-small-models.pdf"),
    ("2311.10227", "prompting", "persona-prompting-not-helpful.pdf"),
    ("2407.01687", "prompting", "cot-factors-probability-memorization.pdf"),
    ("2310.11324", "prompting", "format-spread-prompt-sensitivity.pdf"),
    ("2405.10276", "prompting", "revisiting-opro-small-llm-optimizers.pdf"),
    # llm/prompting papers also in prompting/
    ("2501.04682", "prompting", "meta-chain-of-thought-system2-reasoning.pdf"),
    ("2410.14826", "prompting", "sprig-system-prompt-optimization.pdf"),
    ("2505.05315", "prompting", "elastic-reasoning-scalable-cot.pdf"),
    ("2512.02840", "prompting", "promptolution-modular-prompt-optimization.pdf"),
    ("2601.06403", "prompting", "steer-model-system-prompt-adherence.pdf"),
    # -----------------------------------------------------------------------
    # code-retrieval (7 files)
    # -----------------------------------------------------------------------
    ("2506.15655", "code-retrieval", "cast-ast-structural-chunking.pdf"),
    ("2602.11671", "code-retrieval", "hydra-structure-aware-code-indexing.pdf"),
    ("2509.16112", "code-retrieval", "coderag-repo-level-completion.pdf"),
    ("2510.24749", "code-retrieval", "reflectcode-dual-encoder-retrieval.pdf"),
    ("2411.12644", "code-retrieval", "codexembed-code-embedding-models.pdf"),
    ("2405.19782", "code-retrieval", "draco-dataflow-python-retrieval.pdf"),
    ("2503.20589", "code-retrieval", "alliancecoder-what-to-retrieve.pdf"),
    # -----------------------------------------------------------------------
    # workflows (16 files)
    # -----------------------------------------------------------------------
    ("2601.22290", "workflows", "six-sigma-agent-consensus-redundancy.pdf"),
    ("2602.16873", "workflows", "adaptorch-task-adaptive-orchestration.pdf"),
    ("2406.01297", "workflows", "self-correction-when-it-works.pdf"),
    ("2603.01213", "workflows", "can-ai-agents-agree-consensus.pdf"),
    ("2603.00532", "workflows", "denoiseflow-multi-step-reasoning.pdf"),
    ("2512.23712", "workflows", "sted-structured-output-consistency.pdf"),
    ("2504.05047", "workflows", "down-debate-only-when-necessary.pdf"),
    ("2512.24933", "workflows", "adopt-adaptive-dependency-prompt-optimization.pdf"),
    ("2602.17633", "workflows", "when-to-trust-cheap-check-verification.pdf"),
    ("2602.06039", "workflows", "dytopo-dynamic-topology-routing.pdf"),
    ("2409.12147", "workflows", "magicore-multi-agent-coarse-to-fine-refinement.pdf"),
    ("2510.01499", "workflows", "higher-order-info-multi-model-voting.pdf"),
    ("2602.02828", "workflows", "pacer-consensus-packet-revision.pdf"),
    # "Reaching Agreement Among Reasoning LLM Agents" — same paper, different filename
    ("2512.20184", "workflows", "aegean-protocol-incremental-quorum.pdf"),
    ("2503.07675", "workflows", "dyntaskmas-dynamic-task-graphs.pdf"),
    ("2603.04428", "workflows", "prompt-choreography-kv-cache-multiagent.pdf"),
    # -----------------------------------------------------------------------
    # graph-modeling (18 files — IDs from embedded arXiv headers)
    # -----------------------------------------------------------------------
    ("2302.05019", "graph-modeling", "automatic-kg-construction-survey.pdf"),
    ("2406.11160", "graph-modeling", "context-graph-beyond-triples.pdf"),
    ("2504.05767", "graph-modeling", "cross-document-coreference-kg.pdf"),
    ("2409.01102", "graph-modeling", "gql-sql-pgq-formal-foundations.pdf"),
    ("2308.06512", "graph-modeling", "hyperformer-hyper-relational-kg.pdf"),
    (
        "2404.09848",
        "graph-modeling",
        "hypermono-stage-reasoning-qualifier-monotonicity.pdf",
    ),
    ("2502.06472", "graph-modeling", "karma-multi-agent-kg-enrichment.pdf"),
    ("2310.04835", "graph-modeling", "knowledge-graph-evolution-survey.pdf"),
    ("2510.20345", "graph-modeling", "llm-empowered-kg-construction-survey.pdf"),
    ("2410.13813", "graph-modeling", "meta-property-graphs-iso-extension.pdf"),
    ("2411.18847", "graph-modeling", "mv4pg-materialized-views-property-graphs.pdf"),
    ("2512.01092", "graph-modeling", "pg-hive-incremental-schema-discovery.pdf"),
    ("2307.07354", "graph-modeling", "pg-triggers-active-database-graphs.pdf"),
    ("2309.03685", "graph-modeling", "pygraft-configurable-schema-generation.pdf"),
    ("2603.04184", "graph-modeling", "rdb2rdf-enterprise-kg-maintenance.pdf"),
    ("2110.13348", "graph-modeling", "rdf-to-property-graph-tradeoffs.pdf"),
    ("2404.12788", "graph-modeling", "rexel-end-to-end-document-ie.pdf"),
    ("2502.01295", "graph-modeling", "shacl-shex-pgschema-common-foundations.pdf"),
    # -----------------------------------------------------------------------
    # temporal-provenance (10 of 14 files — 4 are in PENDING above)
    # -----------------------------------------------------------------------
    (
        "2111.13499",
        "temporal-provenance",
        "bitemporal-property-graphs-evolving-systems.pdf",
    ),
    ("2409.04499", "temporal-provenance", "conver-g-concurrent-kg-versioning.pdf"),
    (
        "2601.05270",
        "temporal-provenance",
        "livevectorlake-versioned-knowledge-base.pdf",
    ),
    (
        "2511.06179",
        "temporal-provenance",
        "memoriesdb-temporal-semantic-relational.pdf",
    ),
    ("2409.20302", "temporal-provenance", "om4ov-ontology-matching-versioning.pdf"),
    ("2412.07986", "temporal-provenance", "provenance-semirings-first-order-logic.pdf"),
    (
        "2403.04782",
        "temporal-provenance",
        "temporal-kg-representation-learning-survey.pdf",
    ),
    (
        "2505.11803",
        "temporal-provenance",
        "vita-versatile-time-hyper-relational-tkg.pdf",
    ),
    ("2504.19757", "temporal-provenance", "vmodb-unified-event-data-management.pdf"),
    ("2501.13956", "temporal-provenance", "zep-temporal-kg-agent-memory.pdf"),
    # -----------------------------------------------------------------------
    # belief-consistency (15 files — IDs from embedded arXiv headers)
    # -----------------------------------------------------------------------
    ("2112.13557", "belief-consistency", "agm-belief-revision-tarskian-logics.pdf"),
    ("2508.02426", "belief-consistency", "bake-bayesian-continual-kge.pdf"),
    ("2510.10042", "belief-consistency", "belief-graphs-reasoning-zones.pdf"),
    ("2512.22318", "belief-consistency", "cagp-decomposed-uncertainty-kge.pdf"),
    (
        "2510.24754",
        "belief-consistency",
        "certainty-in-uncertainty-statistical-guarantees.pdf",
    ),
    (
        "2505.16877",
        "belief-consistency",
        "condkgcp-predicate-conditional-conformal.pdf",
    ),
    ("2511.11118", "belief-consistency", "continual-kge-informed-initialization.pdf"),
    ("2509.15464", "belief-consistency", "evoreasoner-evokg-temporal-evolving.pdf"),
    ("2502.16514", "belief-consistency", "graphcheck-kg-powered-fact-checking.pdf"),
    ("2502.19023", "belief-consistency", "inconsistency-kg-reasoning-survey.pdf"),
    ("2403.10502", "belief-consistency", "minimal-surprise-belief-change.pdf"),
    ("2503.08298", "belief-consistency", "progressive-entity-resolution.pdf"),
    ("2503.08087", "belief-consistency", "resolvi-reference-architecture-er.pdf"),
    ("2410.08985", "belief-consistency", "trustworthy-kg-reasoning-uag.pdf"),
    ("2511.10375", "belief-consistency", "truthfulrag-factual-conflict-resolution.pdf"),
    # -----------------------------------------------------------------------
    # knowledge-management (10 files — NEW corpus, kept for completeness)
    # These will be skipped since files already exist.
    # -----------------------------------------------------------------------
    (
        "2506.23826",
        "knowledge-management",
        "digital-me-personal-human-digital-twins.pdf",
    ),
    (
        "2304.09572",
        "knowledge-management",
        "ecosystem-personal-knowledge-graphs-survey.pdf",
    ),
    ("2602.20507", "knowledge-management", "indaleko-unified-personal-index.pdf"),
    ("2512.12686", "knowledge-management", "memoria-scalable-agentic-memory.pdf"),
    ("2509.03610", "knowledge-management", "notebar-ai-assisted-note-taking.pdf"),
    ("2204.11428", "knowledge-management", "personal-research-knowledge-graphs.pdf"),
    (
        "2506.17001",
        "knowledge-management",
        "personalai-kg-storage-retrieval-comparison.pdf",
    ),
    (
        "2508.10906",
        "knowledge-management",
        "personatwin-multi-tier-prompt-conditioning.pdf",
    ),
    ("2503.08102", "knowledge-management", "second-me-ai-native-memory.pdf"),
    ("2409.13265", "knowledge-management", "towards-lifespan-cognitive-systems.pdf"),
    ("2404.16130", "knowledge-management", "graphrag-microsoft.pdf"),
    ("2405.14831", "knowledge-management", "hipporag-neuroinspired-memory.pdf"),
    ("2310.08560", "knowledge-management", "memgpt-llms-as-operating-systems.pdf"),
    ("1709.04999", "knowledge-management", "knowledge-graph-completion-survey.pdf"),
    ("2309.02427", "knowledge-management", "cognitive-architectures-llm-agents.pdf"),
]

ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"

RATE_LIMIT_DELAY = 3.0  # seconds between downloads


async def download_pdf(
    arxiv_id: str,
    target_dir: str,
    filename: str,
    client: httpx.AsyncClient,
    dry_run: bool = False,
) -> bool:
    dest = RESEARCH_ROOT / target_dir / filename
    if dest.exists() and dest.stat().st_size > 10_000:
        print(f"  SKIP (exists): {target_dir}/{filename}")
        return True

    url = ARXIV_PDF_URL.format(arxiv_id=arxiv_id)
    if dry_run:
        print(f"  DRY-RUN: {arxiv_id} → {target_dir}/{filename}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  GET {arxiv_id} → {target_dir}/{filename}")
    try:
        resp = await client.get(url, timeout=60, follow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 10_000:
            dest.write_bytes(resp.content)
            print(f"    ✓ {len(resp.content) // 1024}KB")
            return True
        else:
            print(f"    ✗ HTTP {resp.status_code}, size={len(resp.content)}")
            return False
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return False


async def main(dry_run: bool = False) -> None:
    print(f"\nRestoring {len(ARXIV_PAPERS)} papers to {RESEARCH_ROOT}")
    print("Papers in PENDING (not on arXiv, must be sourced manually):")
    print("  - rag-systems/cormack-rrf-2009.pdf  (SIGIR 2009)")
    print("  - temporal-provenance/acl2020-provenance-claims.pdf  (ACL 2020)")
    print("  - temporal-provenance/acl2021-fine-grained-provenance.pdf  (ACL 2021)")
    print("  - temporal-provenance/green2007-provenance-semirings.pdf  (PODS 2007)")
    print(
        "  - temporal-provenance/provsql-semiring-provenance-postgresql.pdf  (VLDB 2018)"
    )

    ok = failed = skipped = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": "research-corpus-restore/1.0 (academic use)"},
        follow_redirects=True,
    ) as client:
        print(f"\n{'=' * 60}")
        print(f"Downloading {len(ARXIV_PAPERS)} papers")
        print(f"{'=' * 60}")

        for arxiv_id, target_dir, filename in ARXIV_PAPERS:
            dest = RESEARCH_ROOT / target_dir / filename
            if dest.exists() and dest.stat().st_size > 10_000:
                skipped += 1
                print(f"  SKIP: {target_dir}/{filename}")
                continue
            success = await download_pdf(
                arxiv_id, target_dir, filename, client, dry_run
            )
            if success:
                ok += 1
            else:
                failed += 1
            if not dry_run:
                await asyncio.sleep(RATE_LIMIT_DELAY)

    print(f"\n{'=' * 60}")
    print(f"Done: {ok} downloaded, {skipped} skipped (exist), {failed} failed")
    print("PENDING (not on arXiv, source manually): 5 files")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY-RUN mode — no files will be written")
    asyncio.run(main(dry_run=dry_run))
