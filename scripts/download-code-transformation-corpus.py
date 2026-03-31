#!/usr/bin/env python3
"""Batch download code transformation research papers and register with RAG.

Downloads 30 papers from arXiv (28), IEEE (1), and OpenReview (1) into
docs/research/code-transformation/ and registers article metadata via
Stargate POST /api/v1/rag/article.

Source: agent-bus thread 362 — modularize pipeline research corpus.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

SUBDIR = "code-transformation"
SCOPE = "code_transformation"
INGEST = "scripts/ingest-article"


@dataclass(slots=True)
class Paper:
    arxiv_id: str | None
    url: str | None
    filename: str
    title: str

    @property
    def is_arxiv(self) -> bool:
        return self.arxiv_id is not None


PAPERS: list[Paper] = [
    Paper("2603.04177", None, "codetaste-llm-refactoring-quality.pdf",
          "CodeTaste: Can LLMs Generate Human-Level Code Refactorings?"),
    Paper("2602.03712", None, "swe-refactor-benchmark.pdf",
          "SWE-Refactor: A Repository-Level Benchmark for Real-World LLM-Based Code Refactoring"),
    Paper("2401.15298", None, "llm-ide-extract-method.pdf",
          "Together We Go Further: LLMs and IDE Static Analysis for Extract Method Refactoring"),
    Paper("2602.21833", None, "iterative-readability-refactoring.pdf",
          "From Restructuring to Stabilization: Iterative Code Readability Refactoring with LLMs"),
    Paper("2404.00971", None, "hallucinations-llm-code-gen.pdf",
          "Beyond Functional Correctness: Exploring Hallucinations in LLM-Generated Code"),
    Paper("2409.20550", None, "llm-hallucinations-practical-codegen.pdf",
          "LLM Hallucinations in Practical Code Generation: Phenomena, Mechanism, and Mitigation"),
    Paper("2401.01701", None, "de-hallucinator-iterative-grounding.pdf",
          "De-Hallucinator: Mitigating LLM Hallucinations in Code Generation Tasks via Iterative Grounding"),
    Paper("2503.16167", None, "codereviewqa-benchmark.pdf",
          "CodeReviewQA: The Code Review Comprehension Assessment for LLMs"),
    Paper("2501.15134", None, "bitsai-cr-automated-review.pdf",
          "BitsAI-CR: Automated Code Review via LLM in Practice"),
    Paper("2411.10129", None, "prompting-finetuning-code-review.pdf",
          "Prompting and Fine-tuning LLMs for Automated Code Review Comment Generation"),
    Paper("2405.20551", None, "em-assist-safe-extract-method.pdf",
          "EM-Assist: Safe Automated ExtractMethod Refactoring with LLMs"),
    Paper("2503.14340", None, "mantra-multiagent-refactoring-rag.pdf",
          "MANTRA: Enhancing Automated Method-Level Refactoring with Contextual RAG and Multi-Agent LLM Collaboration"),
    Paper("2601.19106", None, "ast-hallucination-detection.pdf",
          "Detecting and Correcting Hallucinations in LLM-Generated Code via Deterministic AST Analysis"),
    Paper("2508.12358", None, "systematic-failures-code-verification.pdf",
          "Uncovering Systematic Failures of LLMs in Verifying Code Against Natural Language Specifications"),
    Paper("2506.11442", None, "reveal-self-evolving-code-agents.pdf",
          "ReVeal: Self-Evolving Code Agents via Iterative Generation-Verification"),
    Paper("2303.11366", None, "reflexion-verbal-reinforcement-learning.pdf",
          "Reflexion: Language Agents with Verbal Reinforcement Learning"),
    Paper("2511.21788", None, "code-refactoring-few-shot-eval.pdf",
          "Code Refactoring with LLM: A Comprehensive Evaluation with Few-Shot Settings"),
    Paper("2305.14752", None, "self-healing-formal-verification.pdf",
          "Towards Self-Healing Software via LLMs and Formal Verification"),
    Paper("2509.02330", None, "recode-fine-grained-rag-repair.pdf",
          "ReCode: Improving LLM-based Code Repair with Fine-Grained RAG"),
    Paper("2405.17503", None, "code-repair-exploration-exploitation.pdf",
          "Code Repair with LLMs gives an Exploration-Exploitation Tradeoff"),
    Paper("2402.07138", None, "llm-tbe-code-change-automation.pdf",
          "Unprecedented Code Change Automation: The Fusion of LLMs and Transformation by Example"),
    Paper("2308.11148", None, "llama-reviewer-code-review.pdf",
          "LLaMA-Reviewer: Advancing Code Review Automation with Large Language Models"),
    Paper("2412.18531", None, "automated-code-review-practice.pdf",
          "Automated Code Review In Practice"),
    Paper("2510.26480", None, "extract-method-open-source-llms.pdf",
          "Automated Extract Method Refactoring with Open-Source LLMs: A Comparative Study"),
    Paper("2503.20934", None, "move-method-llm-ide-embedding.pdf",
          "Together We Are Better: LLM, IDE and Semantic Embedding to Assist Move Method Refactoring"),
    Paper(None, "https://ieeexplore.ieee.org/document/11024270/",
          "muarf-multiagent-method-refactoring.pdf",
          "MUARF: Leveraging Multi-Agent Workflows for Automated Method-Level Refactoring"),
    Paper("2502.09183", None, "refinecoder-adaptive-critique.pdf",
          "RefineCoder: Iterative Improving of LLMs via Adaptive Critique Refinement for Code Generation"),
    Paper(None, "https://openreview.net/pdf?id=0Zri6HSYaK",
          "llm-as-critique-code-gen.pdf",
          "More Than Just Functional: LLM-as-a-Critique for Efficient Code Generation"),
    Paper("2408.08333", None, "codemirage-hallucinations-benchmark.pdf",
          "CodeMirage: Hallucinations in Code Generated by Large Language Models"),
    Paper("2504.20799", None, "hallucination-codegen-taxonomy-survey.pdf",
          "Hallucination by Code Generation LLMs: Taxonomy, Benchmarks, Mitigation, and Challenges"),
]


def ingest(paper: Paper) -> tuple[str, bool]:
    """Call scripts/ingest-article for a single paper. Returns (filename, success)."""
    cmd = [
        sys.executable, INGEST,
        "--subdir", SUBDIR,
        "--filename", paper.filename,
        "--title", paper.title,
        "--scope", SCOPE,
    ]
    if paper.arxiv_id:
        cmd.extend(["--arxiv", paper.arxiv_id])
    elif paper.url:
        cmd.extend(["--url", paper.url])
    else:
        return paper.filename, False

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
    print(f"Downloading {len(PAPERS)} papers to docs/research/{SUBDIR}/\n")

    succeeded: list[str] = []
    failed: list[str] = []

    for i, paper in enumerate(PAPERS, 1):
        tag = f"arXiv:{paper.arxiv_id}" if paper.arxiv_id else "URL"
        print(f"[{i:2d}/{len(PAPERS)}] {tag:20s} {paper.title[:60]}...")
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
