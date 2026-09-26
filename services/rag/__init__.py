"""RAG service package: ChromaDB-backed semantic search and knowledge management.

Intentionally re-exports nothing; import concrete submodules directly. The FastAPI
app lives in ``services.rag.rag_service.main`` (loaded by ``libs/openapi_mcp`` and
``scripts/openapi_mcp_codegen.py``), admin CRUD routes in
``services.rag.admin_routes``, chunkers in ``services.rag.chunkers`` and runtime
settings in ``services.rag.config``. Design stance: index smart, search cheap.
"""
