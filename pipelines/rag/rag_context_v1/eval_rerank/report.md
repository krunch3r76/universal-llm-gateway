# Rerank quality replay (rag-quality-b)

Judgments are this seat's grades of the captured excerpts: 2 answers the
question, 1 is the same topic, 0 is off-topic or boilerplate. Grades live in
`labels.json` and on each fixture row as `relevance`. Excerpts are truncated
and redacted (mail addresses, tool-call logs, a session note). Full context
stayed in the worktree `tmp/` capture and is not committed.

Capture: 28 sequential calls to `run_rag_search` (the function `rag(op=search)`
runs) at `top_k=10`, from `cb67d209` code against live Stargate. Scopes:
`code_retrieval`, `architecture`, `agent_skills_research`, `knowledge_systems`,
`constitutional_ai`, `graph_modeling`, and default. `q05` and `q08` returned
no chunks. Metrics use the 25 queries that have a cross-encoder score and at
least two chunks. Legacy movement reproduced the live order on 22 of those 25;
the other three have tied priors, so the prior order is not fully recoverable.

## E5 — order, same live finals

| order | nDCG@5 | P@3 | relevance@1 | inversions | mean displacement |
|---|---:|---:|---:|---:|---:|
| live (cb67d209 walker) | 0.6160 | 0.7067 | 0.9200 | 9.20 | 2.526 |
| position-greedy | 0.6675 | 0.7333 | 1.2000 | 4.88 | 1.771 |

The greedy order does not lower any of these. Inversions that remain are ones
the ±3 cap forces.

## E6 — fusion, under position-greedy

| fusion | nDCG@5 | P@3 | relevance@1 |
|---|---:|---:|---:|
| floor `max(1, max prior)` (shipped) | 0.6675 | 0.7333 | 1.2000 |
| no 1.0 floor | 0.5987 | 0.6267 | 1.0400 |
| min-max | 0.5829 | 0.5867 | 1.0000 |
| reciprocal rank of prior and ce | 0.6021 | 0.6000 | 0.9200 |

None of the alternatives improves nDCG@5 without lowering P@3. The floor stays.
The docstrings now say so: sub-unit priors do not consume the 0.70 weight, and
that is the behavior the replay selected. Two queries in this set (`q17`,
`q28`) have priors up to 1.2, so the floor does not bind there; the rest are
RRF-scale.

## Small-set cross-encoder cost

One live `POST /api/v1/rerank` of `baai-bge-reranker-v2-m3`: 3 passages in
0.084 s, 10 passages in 0.056 s (warm). Scoring one to three chunks is inside
the existing search, not a latency trade, so cross-encoder mode scores them
and can emit a real `weak_match`. Generative mode still skips sets of three
or fewer.

## E7 — not implemented

`/health` already returns `phase` once the process accepts connections.
`_startup` opens Chroma, the property index, and the article registry before
that. A fast warming answer means binding the port first and moving that work
to a background task, and not calling `collection.count()` on the health path.
That changes startup failure semantics, so it is a spec, not this diff.
