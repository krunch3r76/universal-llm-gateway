#!/usr/bin/env python3
"""Batch download knowledge systems expansion papers and register with RAG.

23 papers across 5 existing subdirectories:
- knowledge-management (10)
- graph-modeling (2)
- temporal-provenance (2)
- event-salience (2)
- rag-systems (5)
- belief-consistency (1)
- information-extraction (1)

Source: agent-bus thread 363 (13 non-duplicate) + Cursor search (10 additional).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

INGEST = "scripts/ingest-article"


@dataclass(slots=True)
class Paper:
    arxiv_id: str
    filename: str
    title: str
    subdir: str
    scope: str


PAPERS: list[Paper] = [
    # --- Thread 363 papers (13, after dedup) ---
    Paper("2407.04363", "arigraph-episodic-memory-kg-agents.pdf",
          "AriGraph: Learning KG World Models with Episodic Memory for LLM Agents",
          "event-salience", "event_salience"),
    Paper("2510.09156", "agentic-kgr-coevolutionary-multi-agent-rl.pdf",
          "Agentic-KGR: Co-evolutionary KG Construction through Multi-Agent RL",
          "graph-modeling", "graph_modeling"),
    Paper("2603.06290", "epistwin-personal-kg-neuro-symbolic.pdf",
          "EpisTwin: A KG-Grounded Neuro-Symbolic Architecture for Personal AI",
          "knowledge-management", "knowledge_systems"),
    Paper("2505.11140", "follow-the-path-kg-factuality.pdf",
          "Follow the Path: Reasoning over KG Paths for LLM Factuality",
          "rag-systems", "rag_systems"),
    Paper("2502.13247", "grounding-llm-reasoning-kg.pdf",
          "Grounding LLM Reasoning with Knowledge Graphs",
          "rag-systems", "rag_systems"),
    Paper("2603.13264", "federated-personal-kg-lightweight-llms.pdf",
          "Federated Personal KG Completion with Lightweight LLMs",
          "knowledge-management", "knowledge_systems"),
    Paper("2503.13514", "rag-kg-il-incremental-hallucination-reduction.pdf",
          "RAG-KG-IL: Multi-Agent Hybrid for Reducing Hallucinations via Incremental KG",
          "rag-systems", "rag_systems"),
    Paper("2405.03480", "personal-laps-multi-session-search.pdf",
          "Doing Personal LAPS: LLM-Augmented Multi-Session Conversational Search",
          "knowledge-management", "knowledge_systems"),
    Paper("2602.05818", "tkg-thinker-dynamic-reasoning-temporal-kg.pdf",
          "TKG-Thinker: Dynamic Reasoning over Temporal KGs via Agentic RL",
          "temporal-provenance", "temporal_provenance"),
    Paper("2502.12110", "a-mem-agentic-memory-zettelkasten.pdf",
          "A-MEM: Agentic Memory for LLM Agents",
          "event-salience", "event_salience"),
    Paper("2601.01885", "agemem-unified-ltm-stm-management.pdf",
          "AgeMem: Learning Unified LTM and STM Memory Management",
          "knowledge-management", "knowledge_systems"),
    Paper("2507.05257", "evaluating-memory-llm-agents-benchmark.pdf",
          "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions",
          "knowledge-management", "knowledge_systems"),
    Paper("2306.08302", "unifying-llms-kg-roadmap.pdf",
          "Unifying LLMs and Knowledge Graphs: A Roadmap",
          "graph-modeling", "graph_modeling"),

    # --- Cursor search additions (10) ---
    Paper("2603.17244", "kumiho-graph-native-belief-revision.pdf",
          "Graph-Native Cognitive Memory for AI Agents: Formal Belief Revision Semantics for Versioned Memory Architectures",
          "belief-consistency", "belief_consistency"),
    Paper("2601.02744", "synapse-episodic-semantic-spreading-activation.pdf",
          "SYNAPSE: Empowering LLM Agents with Episodic-Semantic Memory via Spreading Activation",
          "knowledge-management", "knowledge_systems"),
    Paper("2602.13530", "remem-reasoning-episodic-memory.pdf",
          "REMem: Reasoning with Episodic Memory in Language Agent",
          "knowledge-management", "knowledge_systems"),
    Paper("2603.19595", "all-mem-lifelong-memory-topology-evolution.pdf",
          "All-Mem: Agentic Lifelong Memory via Dynamic Topology Evolution",
          "knowledge-management", "knowledge_systems"),
    Paper("2505.07509", "halo-half-life-outdated-fact-filtering.pdf",
          "HALO: Half Life-Based Outdated Fact Filtering in Temporal Knowledge Graphs",
          "temporal-provenance", "temporal_provenance"),
    Paper("2512.15922", "spreading-activation-kg-rag-retrieval.pdf",
          "Leveraging Spreading Activation for Improved Document Retrieval in KG-Based RAG Systems",
          "rag-systems", "rag_systems"),
    Paper("2602.05152", "rag-without-forgetting-evolving-memory.pdf",
          "RAG without Forgetting: Continual Query-Infused Key Memory",
          "rag-systems", "rag_systems"),
    Paper("2602.02007", "xmemory-hierarchical-memory-organization.pdf",
          "xMemory: Hierarchical Memory Organization for LLM Agents",
          "knowledge-management", "knowledge_systems"),
    Paper("2603.15642", "cranimem-neurocognitive-memory-design.pdf",
          "CraniMem: Neurocognitively-Inspired Memory Architecture for LLM Agents",
          "knowledge-management", "knowledge_systems"),
    Paper("2601.20465", "bmam-decomposed-memory-subsystems.pdf",
          "BMAM: Decomposed Agent Memory Subsystems for Multi-Scale Reasoning",
          "knowledge-management", "knowledge_systems"),
]


def ingest(paper: Paper) -> tuple[str, bool]:
    cmd = [
        sys.executable, INGEST,
        "--arxiv", paper.arxiv_id,
        "--subdir", paper.subdir,
        "--filename", paper.filename,
        "--title", paper.title,
        "--scope", paper.scope,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAIL: {paper.filename}")
        if result.stderr:
            for line in result.stderr.strip().splitlines()[-3:]:
                print(f"    {line}")
        if result.stdout:
            for line in result.stdout.strip().splitlines()[-3:]:
                print(f"    {line}")
        return paper.filename, False

    print(f"  OK:   {paper.filename}")
    return paper.filename, True


def main() -> int:
    print(f"Downloading {len(PAPERS)} papers across multiple subdirectories\n")

    succeeded: list[str] = []
    failed: list[str] = []

    for i, paper in enumerate(PAPERS, 1):
        print(f"[{i:2d}/{len(PAPERS)}] {paper.subdir:25s} {paper.title[:55]}...")
        fname, ok = ingest(paper)
        (succeeded if ok else failed).append(fname)

    print(f"\n--- Results ---")
    print(f"Succeeded: {len(succeeded)}/{len(PAPERS)}")
    if failed:
        print(f"Failed:    {len(failed)}")
        for f in failed:
            print(f"  - {f}")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
