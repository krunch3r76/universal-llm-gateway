"""LLM scope vocabulary classification and automatic gap repair.

Shared between the CLI script and RAG lifecycle (startup / reconcile / watcher).
"""

from __future__ import annotations

import json
import logging

import httpx

from services.rag.corpus_hints import update_corpus_hints
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.rag.property_index import PropertyIndex
    from services.rag.config import RagConfig
    from universal_event_bus import EventBus

logger = logging.getLogger(__name__)

DEFAULT_STARGATE_MODELS_URL = "http://localhost:9999/v1/models"
DEFAULT_STARGATE_CHAT_URL = "http://localhost:9999/v1/chat/completions"

CLASSIFICATION_PROMPT = """\
You are classifying vocabulary terms for a multi-domain RAG retrieval system.
Given a scope name, its description, and a list of IDF-scored terms extracted
from that scope's corpus, classify each term into one of these registers:

- **practitioner**: tools, platforms, libraries, implementation patterns,
  products, file formats, CLI commands, industry workflow terminology —
  things a working professional in the domain uses day-to-day.
  Examples by domain:
  · Knowledge systems: Obsidian, Neo4j, Cypher, vault, backlinks, Zettelkasten
  · Extraction & NLP: staging queue, surface form cache, structured extraction,
    HITL review, claim-evidence pairs, coreference pipeline
  · Trading/finance: Bloomberg, TradingView, backtesting, order book, Greeks,
    delta hedging, pairs trading, TWAP, VWAP
  · ML/AI: PyTorch, wandb, LoRA, fine-tuning, prompt engineering
  · Code & docs: AST chunking, docstring extraction, doc-code alignment,
    multi-agent workflow, tool registry
  · RAG & retrieval: vector store, reranker, hybrid search, chunk scoring,
    scope vocabulary, corpus hints

- **academic**: formal concepts, theoretical frameworks, named models/theorems,
  algorithmic families, research methodology terms — things you'd find defined
  in a textbook or seminal paper.
  Examples by domain:
  · Knowledge systems: ontology, reification, knowledge graph completion,
    entity alignment, bitemporal modeling
  · Extraction & NLP: entity resolution, coreference resolution, claim
    decomposition, multi-observer consensus, provenance tracing, belief revision
  · Trading/finance: Almgren-Chriss, Kelly criterion, Ornstein-Uhlenbeck,
    cointegration, VPIN, stochastic control, mean reversion
  · ML/AI: attention mechanism, chain-of-thought, in-context learning, RLHF
  · Code & docs: repository-level summarization, code-comment inconsistency,
    hierarchical code representation
  · RAG & retrieval: dense retrieval, iterative grounding, query decomposition,
    cascading model orchestration

- **specification**: named standards, protocol names, regulatory frameworks,
  specification documents, formal schema identifiers — things with an issuing
  body or version number.
  Examples by domain:
  · Knowledge systems: RDF, OWL, SHACL, JSON-LD, SPARQL, SQL/PGQ
  · Extraction & NLP: W3C PROV, PROV-O, PROV-DM, CoNLL format
  · Trading/finance: FIX protocol, FpML, Reg NMS, MiFID II, ISDA, ISO 10962
  · ML/AI: ONNX, OpenAI API, GGUF, safetensors
  · Code & docs: OpenAPI, JSDoc, Sphinx, MCP protocol
  · RAG & retrieval: ChromaDB, MTEB benchmark, BEIR

Rules:
1. A term may appear in only one register (choose the best fit).
2. DROP noise — these are never useful vocabulary:
   - Single letters or bare symbols (a, r, x, θ)
   - Document structure references (theorem 4.1, lemma a.1, figure 4, table 2)
   - Author citation fragments (et al., guijarro-ordonez et al. (2021))
   - Mathematical variable names without semantic meaning (z[q], θ[q])
   - Overly generic words (model, system, data, method, results, approach)
3. Use the scope description to guide domain-appropriate classification.
   The same term can belong to different registers in different domains.
4. You may add 2-4 additional high-value terms per register that are
   obviously missing but central to the scope. Mark these with a trailing
   asterisk (*) so the caller knows they were inferred.
5. Return valid JSON only.

Output format:
{
  "practitioner": ["term1", "term2", ...],
  "academic": ["term1", "term2", ...],
  "specification": ["term1", "term2", ...]
}
"""


def configured_scopes_map(config: RagConfig) -> dict[str, list[str]]:
    """Map scope name → prefix list from rag.yaml."""
    return {name: list(sdef.prefixes) for name, sdef in config.scopes.items()}


async def pick_loaded_stargate_model(
    client: httpx.AsyncClient,
    *,
    models_url: str = DEFAULT_STARGATE_MODELS_URL,
) -> str | None:
    """Return a gateway-owned model id, or None if Stargate is unreachable."""
    try:
        resp = await client.get(models_url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.warning(
            "Stargate models probe failed due to HTTP error: %s", e, exc_info=True
        )
        return None
    except json.JSONDecodeError as e:
        logger.warning(
            "Stargate models probe failed due to JSON decoding error: %s",
            e,
            exc_info=True,
        )
        return None
    except Exception:
        logger.warning(
            "Stargate models probe failed due to unexpected error", exc_info=True
        )
        return None
    models = data.get("data") or []
    owned_model_ids = [
        m["id"]
        for m in models
        if isinstance(m, dict)
        and isinstance(m.get("id"), str)
        and m["id"]
        and m.get("owned_by") == "universal-llm-gateway"
    ]
    if not owned_model_ids:
        return None

    preferred_prefixes = ("qwen3-5-27b", "qwen3-5-14b", "qwen3-14b", "qwen3")
    for pref in preferred_prefixes:
        for mid in owned_model_ids:
            if pref in mid:
                return mid

    return owned_model_ids[0]


async def classify_scope_async(
    *,
    scope: str,
    description: str,
    terms: list[str],
    model: str,
    chat_url: str = DEFAULT_STARGATE_CHAT_URL,
    client: httpx.AsyncClient | None = None,
) -> dict[str, list[str]] | None:
    """Classify terms via Stargate chat completions (async)."""
    user_msg = (
        f"Scope: {scope}\n"
        f"Description: {description}\n"
        f"Terms to classify:\n{json.dumps(terms)}\n\n"
        "Return JSON with keys: practitioner, academic, specification."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CLASSIFICATION_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }
    own_client = client is None
    hc = client or httpx.AsyncClient(timeout=120.0)
    try:
        resp = await hc.post(chat_url, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        clean: dict[str, list[str]] = {
            reg: [
                str(t) for t in parsed.get(reg, []) if isinstance(t, str) and t.strip()
            ]
            for reg in ("practitioner", "academic", "specification")
        }
        return clean
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.exception(
            "Classification failed for scope '%s' due to HTTP error: %s", scope, e
        )
        return None
    except (KeyError, json.JSONDecodeError) as e:
        logger.exception(
            "Classification failed for scope '%s' due to JSON parsing error: %s",
            scope,
            e,
        )
        return None
    except Exception as e:
        logger.exception(
            "Classification failed for scope '%s' due to unexpected error: %s", scope, e
        )
        return None
    finally:
        if own_client and hc is not None:
            await hc.aclose()


async def run_scope_freshness_repair(
    *,
    property_index: PropertyIndex,
    config: RagConfig,
    stale_scopes: list[str],
    event_bus: EventBus | None,
    trigger: str,
    models_url: str = DEFAULT_STARGATE_MODELS_URL,
    chat_url: str = DEFAULT_STARGATE_CHAT_URL,
) -> None:
    """Refresh corpus hints and vocabulary for scopes whose file-set hash drifted.

    Always uses per-scope classification (lightweight, local model). The full
    pipeline (vocab-classify-v1) is reserved for explicit ``--mode frontier``
    invocations via ``scripts/rag/classify_vocabulary.py``.
    """
    if not stale_scopes:
        return

    from services.rag.events.lifecycle import (
        rag_hints_gaps_repaired,
        rag_vocabulary_gaps_detected,
        rag_vocabulary_gaps_repaired,
    )

    cs_map = configured_scopes_map(config)

    hints_updated: list[str] = []
    vocab_ok: list[str] = []
    vocab_failed: list[str] = []
    no_model_scopes: list[str] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        model = await pick_loaded_stargate_model(client, models_url=models_url)

        for scope_name in stale_scopes:
            if scope_name not in cs_map:
                continue
            await update_corpus_hints(
                property_index,
                scope=scope_name,
                configured_scopes=cs_map,
                event_bus=event_bus,
            )
            hints_updated.append(scope_name)

        classify_scopes = list(set(stale_scopes) & set(cs_map.keys()))
        if not classify_scopes:
            pass
        elif model is None:
            for scope_name in classify_scopes:
                no_model_scopes.append(scope_name)
                logger.warning(
                    "Scope %s: hints refreshed but no Stargate model — "
                    "vocabulary skipped (stale until model available)",
                    scope_name,
                )
        else:
            from services.rag.corpus_hints import (
                compute_scope_files_hash,
                load_corpus_hints,
            )

            hints_map = load_corpus_hints()
            descriptions = {
                n: getattr(sdef, "description", "") or ""
                for n, sdef in config.scopes.items()
            }

            for scope_name in sorted(classify_scopes):
                text = hints_map.get(scope_name, "")
                terms = [t.strip() for t in text.split(",") if t.strip()]
                if not terms:
                    vocab_failed.append(scope_name)
                    continue
                desc = descriptions.get(scope_name, "")
                try:
                    result = await classify_scope_async(
                        scope=scope_name,
                        description=desc,
                        terms=terms,
                        model=model,
                        chat_url=chat_url,
                        client=client,
                    )
                except Exception as e:
                    logger.warning(
                        "Per-scope classify failed for %s: %s",
                        scope_name,
                        e,
                    )
                    result = None

                if result is None:
                    vocab_failed.append(scope_name)
                    continue

                await property_index.replace_scope_vocabulary_for_scopes(
                    {scope_name: result}
                )
                fh = compute_scope_files_hash(property_index, cs_map[scope_name])
                await property_index.store_scope_freshness(
                    scope_name, fh, classified_tier="local"
                )
                vocab_ok.append(scope_name)

            if vocab_ok:
                await property_index.stamp_watermark("vocabulary")

            if vocab_failed:
                logger.warning(
                    "Per-scope classify failed for %d scope(s): %s",
                    len(vocab_failed),
                    ", ".join(vocab_failed),
                )
            if vocab_ok:
                logger.info(
                    "Per-scope classify completed for %d scope(s)",
                    len(vocab_ok),
                )

    if hints_updated and event_bus is not None:
        await event_bus.publish_async_nowait(
            rag_hints_gaps_repaired(scopes=hints_updated, trigger=trigger)
        )

    if no_model_scopes and event_bus is not None:
        await event_bus.publish_async_nowait(
            rag_vocabulary_gaps_detected(
                scopes=no_model_scopes,
                reason="no_model_available",
            )
        )

    if vocab_ok and event_bus is not None:
        await event_bus.publish_async_nowait(
            rag_vocabulary_gaps_repaired(
                scopes=sorted(vocab_ok),
                model=model or "local",
            )
        )

    if hints_updated:
        await property_index.stamp_watermark("corpus_hints")

    if vocab_failed:
        logger.error(
            "Vocabulary classification failed for scopes: %s",
            ", ".join(vocab_failed),
        )


__all__ = [
    "CLASSIFICATION_PROMPT",
    "DEFAULT_STARGATE_CHAT_URL",
    "DEFAULT_STARGATE_MODELS_URL",
    "classify_scope_async",
    "configured_scopes_map",
    "pick_loaded_stargate_model",
    "run_scope_freshness_repair",
]
