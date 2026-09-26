"""Operator scripts for the RAG service, run as modules rather than imported.

Currently holds ``rag_corpus_id_reindex``, the one-time CLI sweep that
force-reindexes every indexed source so chunk IDs move to the content-addressed
``{path_key}-{chunk_hash}`` scheme. The package exposes no re-exported names.
"""
