"""RAG event factories package.

This package centralizes typed event factories for RAG lifecycle, indexing,
extraction, query, and article metadata flows. Factories are grouped by domain
so producers can emit stable signals with consistent payload contracts:

- ``services.rag.events.lifecycle`` for service/watcher startup and shutdown
- ``services.rag.events.extraction`` for chunk-level and batch extraction outcomes
- ``services.rag.events.indexing`` for indexing/delete/normalization transitions
- ``services.rag.events.query`` for scope resolution, retrieval, and hints updates
- ``services.rag.events.articles`` for article metadata upsert operations
"""
