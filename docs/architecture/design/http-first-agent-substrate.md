# HTTP-first agent substrate — public pointer

**Status:** thin public pointer (organizational, not doctrinal). The doctrinal SoT is
`decision:http-first-agent-substrate`; this page is its smallest honest public home.
**Bound to:** `decision:http-first-agent-substrate` · posture-stack foundation map
`cortex://notes/system/threads/4917-posture-stack-foundation/fable-foundation-map.md` (Pillar 3).
**Do not restate law here** — cite the decision and the sources; patch this page when the graph changes.

---

## Thesis

HTTP is the agent tooling substrate with **served, typed-args schemas** (OpenAPI, pulled — not
pushed). This is **not** "HTTP replaces MCP": MCP remains a client adapter, not the ontology.
Argument-grounding — instance-specific argument construction — is the dominant LLM tool-use
failure axis, so per-operation typed schemas stay load-bearing; HTTP-first relocates them
server-side and serves them on demand, and at high endpoint counts (N) endpoint selection
becomes a retrieval problem (HTTP + OpenAPI + endpoint-RAG). Adopted in
`decision:http-first-agent-substrate` (assertions 17034, 17059); the lineage and the
Semantic-Web-heir framing are grounded in assertions 17587 and 17588.

The web was explicitly architected for non-human clients discovering and using resources; the
LLM is the limit case of that client. OpenAPI is the heir to the Semantic Web's goal via the
*opposite* mechanism: RDF/OWL pushed ontologies onto every resource and failed on annotation
cost; OpenAPI serves typed per-operation schemas on demand to a statistical reader
(assertion 17588; RFC 8631 §6.2 names OpenAPI as the `service-desc` payload, verbatim).

## Primary sources (foundation map §Primary sources — S1–S6)

| id | Source | URI |
|---|---|---|
| S1 | Berners-Lee, Hendler & Lassila, "The Semantic Web," *Scientific American* (May 2001) | https://www.scientificamerican.com/article/the-semantic-web/ |
| S2 | "Semantic Web and Software Agents — A Forgotten Wave of AI?" (arXiv:2503.20793) | https://arxiv.org/abs/2503.20793 |
| S3 | RFC 8631 — *Link Relation Types for Web Services* (`service-desc`) | https://www.rfc-editor.org/rfc/rfc8631 |
| S4 | RFC 8615 — *Well-Known Uniform Resource Identifiers* | https://www.rfc-editor.org/rfc/rfc8615 |
| S5 | RFC 9727 — *api-catalog: A Well-Known URI and Link Relation for API Discovery* | https://www.rfc-editor.org/rfc/rfc9727 |
| S6 | OpenAPI Specification v3.1.1 | https://spec.openapis.org/oas/v3.1.1.html |

Full source table (S1–S16), pillar law, and attachment grammar live in the foundation map.
The C↔S citation concordance and the argument-grounding empirical corpus live in the lineage
memo (S16, below).

## Tracked debt on our own surface

Cortex's MCP-facing dispatch surface (~70 ops routed through `dispatch_ops` as untyped
`**kwargs` handlers) is **not** grounded in served, typed HTTP routes — the named debt this
posture retires, not a contradiction of it (assertion 21528). HTTP-first shrinks this axis; new
tool families must not mint as untyped megatools.

## Pointers

- Doctrinal SoT: `decision:http-first-agent-substrate` (assertions 17034 · 17059 · 17587 · 17588 · 21528).
- Lineage memo (S16 — C1–C11 concordance, argument-grounding corpus): `cortex://notes/system/threads/4917-posture-stack-foundation/http-agent-substrate-lineage.md`.
- Fable HTTP-substrate eval (S7 — decision-grade inline eval): `cortex://notes/system/threads/4917-posture-stack-foundation/fable-http-substrate-eval-RESULT.md`.
- RAG scope `agent_substrate` (HTTP RFCs, Fielding dissertation, OAS 3.1.1, HATEOAS, MCP-vs-REST analyses).
- Foundation map (Pillar 3): `cortex://notes/system/threads/4917-posture-stack-foundation/fable-foundation-map.md`.
