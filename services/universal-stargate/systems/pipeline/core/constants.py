"""Shared sentinel constants for RAG pipeline components.

∀ RAG retrieval sentinel: defined here; imported by both the retrieval handler
(rag_query_retrieve) and any answer handler that needs to detect empty context.
Prevents string drift between producer and consumer.
"""

RAG_NO_RESULTS_SENTINEL: str = "No relevant documents found in the knowledge base."
RAG_NO_RETRIEVAL_SENTINEL: str = "No retrieval needed — answering from model knowledge."
