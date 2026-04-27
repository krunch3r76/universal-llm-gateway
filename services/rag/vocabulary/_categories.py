"""Per-category descriptions and default taxonomy for vocabulary classification."""

from __future__ import annotations

# Per-category descriptions for the classification prompt. Keys are category names;
# values are the descriptive text inserted into the prompt bullet for that category.
# Unknown categories (not in this dict) get a generic "terms characteristic of
# {category} discourse in the domain" description.
_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "practitioner": (
        "tools, platforms, libraries, implementation patterns,\n"
        "  products, file formats, CLI commands, industry workflow terminology —\n"
        "  things a working professional in the domain uses day-to-day.\n"
        "  Examples by domain:\n"
        "  · Knowledge systems: Obsidian, Neo4j, Cypher, vault, backlinks, Zettelkasten\n"
        "  · Extraction & NLP: staging queue, surface form cache, structured extraction,\n"
        "    HITL review, claim-evidence pairs, coreference pipeline\n"
        "  · Trading/finance: Bloomberg, TradingView, backtesting, order book, Greeks,\n"
        "    delta hedging, pairs trading, TWAP, VWAP\n"
        "  · ML/AI: PyTorch, wandb, LoRA, fine-tuning, prompt engineering\n"
        "  · Code & docs: AST chunking, docstring extraction, doc-code alignment,\n"
        "    multi-agent workflow, tool registry\n"
        "  · RAG & retrieval: vector store, reranker, hybrid search, chunk scoring,\n"
        "    scope vocabulary, corpus hints"
    ),
    "academic": (
        "formal concepts, theoretical frameworks, named models/theorems,\n"
        "  algorithmic families, research methodology terms — things you'd find defined\n"
        "  in a textbook or seminal paper.\n"
        "  Examples by domain:\n"
        "  · Knowledge systems: ontology, reification, knowledge graph completion,\n"
        "    entity alignment, bitemporal modeling\n"
        "  · Extraction & NLP: entity resolution, coreference resolution, claim\n"
        "    decomposition, multi-observer consensus, provenance tracing, belief revision\n"
        "  · Trading/finance: Almgren-Chriss, Kelly criterion, Ornstein-Uhlenbeck,\n"
        "    cointegration, VPIN, stochastic control, mean reversion\n"
        "  · ML/AI: attention mechanism, chain-of-thought, in-context learning, RLHF\n"
        "  · Code & docs: repository-level summarization, code-comment inconsistency,\n"
        "    hierarchical code representation\n"
        "  · RAG & retrieval: dense retrieval, iterative grounding, query decomposition,\n"
        "    cascading model orchestration"
    ),
    "specification": (
        "named standards, protocol names, regulatory frameworks,\n"
        "  specification documents, formal schema identifiers — things with an issuing\n"
        "  body or version number.\n"
        "  Examples by domain:\n"
        "  · Knowledge systems: RDF, OWL, SHACL, JSON-LD, SPARQL, SQL/PGQ\n"
        "  · Extraction & NLP: W3C PROV, PROV-O, PROV-DM, CoNLL format\n"
        "  · Trading/finance: FIX protocol, FpML, Reg NMS, MiFID II, ISDA, ISO 10962\n"
        "  · ML/AI: ONNX, OpenAI API, GGUF, safetensors\n"
        "  · Code & docs: OpenAPI, JSDoc, Sphinx, MCP protocol\n"
        "  · RAG & retrieval: ChromaDB, MTEB benchmark, BEIR"
    ),
}

# Default taxonomy used when no config is available. Order = retrieval anchor priority
# (index 0 = highest). Matches the default in RagConfig.vocabulary_taxonomy.
DEFAULT_TAXONOMY: list[str] = ["specification", "practitioner", "academic"]
