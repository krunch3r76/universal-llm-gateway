# Appendix: RAG Vector Store — ChromaDB Choice and Reassessment

## Decision

ChromaDB is used as the vector store for the RAG service.

## Context at Adoption

At the time the RAG service was first built, ChromaDB offered a straightforward
package deal: HNSW indexing, persistence, document co-location, and metadata
filtering — all managed, no hand-rolling required. The alternative (raw FAISS)
would have required building persistence, ID→chunk mapping, and metadata
filtering from scratch.

## What the Architecture Became

The system grew its own metadata and persistence layer independent of ChromaDB:

- **Metadata filtering** — SQLite property index (extracted entities, topics,
  relations per chunk), FTS5 full-text index for Pool B sparse retrieval, and
  the `articles` table joined at query time via `source_hash`. None of this
  delegates to ChromaDB's native metadata filtering.
- **Persistence / system of record** — `rag_metadata.db` (SQLite) carries
  indexed source state, extraction results, corpus hints, scope vocabulary,
  watermarks, and article metadata. ChromaDB's persistent mode is used, but it
  is not the authoritative store.

Most of what ChromaDB "gives you" was rebuilt externally. What ChromaDB now
actually provides is:

1. HNSW index management (insert, persist, query) without hand-rolling it.
2. Chunk text co-located with the embedding vector.

## Honest Assessment

The architectural argument for ChromaDB over raw FAISS (or a lighter HNSW
library like `usearch`) is weaker than it appears once you account for the
external metadata and persistence layer. FAISS is not inferior at this corpus
size — it was never the right comparison axis. The real comparison is:

| | ChromaDB | FAISS / usearch |
|---|---|---|
| HNSW index | Yes | Yes |
| Persistence | Yes (but external SQLite is authoritative) | DIY (already done) |
| Metadata filtering | Yes (unused — external layer handles this) | DIY (already done) |
| Index type flexibility | HNSW only | Many (IVF, PQ, etc.) |
| RAM compression (PQ) | No | Yes (FAISS) |
| Operational overhead | Low | Low, given existing plumbing |

Given the current architecture, switching to raw FAISS or `usearch` at the
vector layer would carry no retrieval quality cost and would remove the
single-node HNSW ceiling without losing anything the system currently relies on
ChromaDB for.

## Why Not Switching Now

- **Migration cost**: the existing Chroma collection would need to be migrated
  and retrieval parity validated.
- **No active pain**: at the current corpus size (~3,400 indexed files), HNSW
  single-node is not a bottleneck.

## Natural Migration Trigger

If either of these becomes true, the migration argument becomes compelling:

- Corpus grows to where single-node HNSW latency or memory becomes a ceiling.
- PQ vector compression is needed to keep the index in RAM.

At that point the switching cost is justified and the external plumbing already
built makes FAISS a clean drop-in at the vector layer.
