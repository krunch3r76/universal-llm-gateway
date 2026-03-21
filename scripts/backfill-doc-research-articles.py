#!/usr/bin/env python3
"""Direct SQLite insert of documentation & software-agents article metadata.

Bypasses the Stargate/RAG API when RAG is not running. Safe to run repeatedly —
uses INSERT OR REPLACE on source_path primary key.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TypedDict

DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"
RESEARCH_ROOT = Path("/mnt/torus/projects/universal-llm-gateway/docs/research")

SUBDIRECTORY_SCOPE = {
    "documentation": "code_documentation",
    "software-agents": "software_agents",
}


class Article(TypedDict):
    subdir: str
    filename: str
    title: str
    authors: str
    published_date: str  # Or datetime.date if parsed


ARTICLES: list[Article] = [
    {
        "subdir": "documentation",
        "filename": "repoagent-repo-level-doc-generation.pdf",
        "title": "RepoAgent: An LLM-Powered Open-Source Framework for Repository-level Code Documentation Generation",
        "authors": "Qinyu Luo, Yining Ye, Shihao Liang, Zhong Zhang, Yujia Qin, Yaxi Lu, Yesai Wu, Xin Cong, Yankai Lin, Yingli Zhang, Xiaoyin Che, Zhiyuan Liu, Maosong Sun",
        "published_date": "2024-02-26",
    },
    {
        "subdir": "documentation",
        "filename": "docagent-multi-agent-doc-generation.pdf",
        "title": "DocAgent: A Multi-Agent System for Automated Code Documentation Generation",
        "authors": "Dayu Yang, Antoine Simoulin, Xintong Hao, Rongwei Luo, Yihan Cao, Kewen Peng, Jiaheng Liu, Siwei Wang, Tianyu Liu",
        "published_date": "2025-04-11",
    },
    {
        "subdir": "documentation",
        "filename": "hierarchical-repo-code-summarization.pdf",
        "title": "Hierarchical Repository-Level Code Summarization for Business Applications Using Local LLMs",
        "authors": "Nilesh Dhawale, Arik Hadas, Atefeh Nirumand, Martin Dzhigansky, Luis Angel Garcia",
        "published_date": "2025-01-14",
    },
    {
        "subdir": "documentation",
        "filename": "code-summarization-beyond-function-level.pdf",
        "title": "Code Summarization Beyond Function Level",
        "authors": "Yichen He, Renyu Zhu, Guochao Jiang, Jun Sun",
        "published_date": "2025-02-24",
    },
    {
        "subdir": "documentation",
        "filename": "codocbench-code-doc-alignment.pdf",
        "title": "CoDocBench: A Dataset for Code-Documentation Alignment in Software Maintenance",
        "authors": "Kunal Pai, Disha, Atul Kumar Ojha",
        "published_date": "2025-02-01",
    },
    {
        "subdir": "documentation",
        "filename": "code2doc-quality-first-dataset.pdf",
        "title": "Code2Doc: A Quality-First Curated Dataset for Function-Documentation Pairs",
        "authors": "Tim Rosenflanz, Andre Bauer",
        "published_date": "2025-12-31",
    },
    {
        "subdir": "documentation",
        "filename": "cast-ast-based-code-rag.pdf",
        "title": "cAST: Code Retrieval-Augmented Generation via AST-Based Chunking",
        "authors": "Jia Li, Yongmin Li, Ge Li, Zhi Jin",
        "published_date": "2025-06-18",
    },
    {
        "subdir": "documentation",
        "filename": "r2comsync-code-comment-sync.pdf",
        "title": "R2ComSync: Code-Comment Synchronization via Hybrid Retrieval",
        "authors": "Xun Zhang, Yiran Hu, Zhiyu Li, Dong Ruan, Lu Chen",
        "published_date": "2025-10-28",
    },
    {
        "subdir": "documentation",
        "filename": "docprism-code-doc-inconsistency.pdf",
        "title": "DocPrism: Code-Documentation Inconsistency Detection",
        "authors": "Anton Shapkin, Filipp Gaidai, Alexandra Klimova, Artyom Lobanov",
        "published_date": "2025-11-01",
    },
    {
        "subdir": "documentation",
        "filename": "llm-doc-code-traceability.pdf",
        "title": "Evaluating LLMs for Documentation to Code Traceability",
        "authors": "Mohammed Shakeel, Horst Lichter",
        "published_date": "2025-06-20",
    },
    {
        "subdir": "software-agents",
        "filename": "de-hallucinator-iterative-grounding.pdf",
        "title": "De-Hallucinator: Iterative Grounding for LLM-Based Code Generation",
        "authors": "Eran Yahav",
        "published_date": "2024-01-03",
    },
    {
        "subdir": "documentation",
        "filename": "codesync-llm-code-evolution.pdf",
        "title": "CODESYNC: Synchronizing LLMs with Evolving Code",
        "authors": "Chengyue Liu, Shichao Sun, Weiwen Xu, Yi Chen, Yong Liu, Lizhen Qu, Qi Zhang, Xuanjing Huang, Wenjie Li",
        "published_date": "2025-02-23",
    },
    {
        "subdir": "software-agents",
        "filename": "ranger-graph-enhanced-retrieval.pdf",
        "title": "RANGER: Graph-Enhanced Repository-Level Code Retrieval",
        "authors": "Jia Li, Yongmin Li, Ge Li, Zhi Jin",
        "published_date": "2025-09-30",
    },
    {
        "subdir": "software-agents",
        "filename": "coderag-repo-level-completion.pdf",
        "title": "CodeRAG: Repository-Level Code Completion via Multi-Path Retrieval",
        "authors": "Ke Chen, Yanlin Wang, Jie Liu, Hao Peng, Ziwen Li",
        "published_date": "2025-09-22",
    },
    {
        "subdir": "software-agents",
        "filename": "arcs-agentic-retrieval-code-synthesis.pdf",
        "title": "ARCS: Agentic Retrieval-Augmented Code Synthesis",
        "authors": "Yue Tan, Shijie Chen, Andrei Paleyes, Neil D. Lawrence",
        "published_date": "2025-04-29",
    },
    {
        "subdir": "software-agents",
        "filename": "toolregistry-protocol-agnostic-tools.pdf",
        "title": "ToolRegistry: Protocol-Agnostic Tool Management for LLM Agents",
        "authors": "",
        "published_date": "2025-07-14",
    },
    {
        "subdir": "software-agents",
        "filename": "oasbuilder-openapi-from-docs.pdf",
        "title": "OASBuilder: Generating OpenAPI Specs from Unstructured API Documentation",
        "authors": "",
        "published_date": "2025-07-07",
    },
    {
        "subdir": "software-agents",
        "filename": "agyn-multi-agent-team-se.pdf",
        "title": "Agyn: Multi-Agent Team-Based Autonomous Software Engineering",
        "authors": "",
        "published_date": "2026-02-02",
    },
]


def main() -> None:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        raise SystemExit(1)

    with sqlite3.connect(str(DB_PATH)) as conn:
        inserted = 0
        for art in ARTICLES:
            pdf_path = RESEARCH_ROOT / art["subdir"] / art["filename"]
            if not pdf_path.exists():
                print(f"  SKIP (missing PDF): {art['filename']}")
                continue
            hasher = hashlib.sha256()
            with open(pdf_path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            content_hash = hasher.hexdigest()
            source_path = str(pdf_path)
            scope = SUBDIRECTORY_SCOPE[art["subdir"]]
            conn.execute(
                "INSERT OR REPLACE INTO articles "
                "(source_path, filename, title, authors, venue, published_date, "
                "doi, abstract, scope, content_hash, subdirectory, comments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_path,
                    art["filename"],
                    art["title"],
                    art["authors"],
                    "",
                    art["published_date"],
                    "",
                    "",
                    scope,
                    content_hash,
                    art["subdir"],
                    "",
                ),
            )
            inserted += 1
            print(f"  OK {art['filename']} -> scope={scope}")
        conn.commit()
    print(f"\nInserted/updated {inserted} rows in {DB_PATH.name}")


if __name__ == "__main__":
    main()
