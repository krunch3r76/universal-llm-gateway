"""LLM scope vocabulary classification and automatic gap repair.

Part of the post-index enrichment pipeline. Classifies terms from corpus hints
into configurable taxonomy categories per scope. The taxonomy is defined in
``RagConfig.vocabulary_taxonomy`` (rag.yaml ``vocabulary_taxonomy`` key); category
order determines retrieval anchor priority. Combined with IDF weighting, this
steers Pool B's corpus expansion toward the most discriminative vocabulary —
terms that distinguish one scope's content from another.

Classification serves two purposes: result categories are injected into generation
prompts so the LLM understands the vocabulary landscape of the corpus, and category
order determines which terms get anchored into retrieval queries first. The
classification cost is paid once at index time; every subsequent query benefits
from vocabulary-aware expansion without LLM calls.

Shared between the CLI script and RAG lifecycle (startup / reconcile / watcher).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from services.rag.corpus_hints import update_corpus_hints

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

DEFAULT_STARGATE_MODELS_URL = "http://localhost:9999/v1/models"
DEFAULT_STARGATE_CHAT_URL = "http://localhost:9999/v1/chat/completions"

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


def build_classification_prompt(taxonomy: list[str]) -> str:
    """Build the system prompt for vocabulary classification from a taxonomy list.

    Known categories use their curated descriptions from _CATEGORY_DESCRIPTIONS.
    Unknown categories get a generic description — add to _CATEGORY_DESCRIPTIONS
    when a new category is introduced to give the LLM better guidance.
    """
    bullets: list[str] = []
    for cat in taxonomy:
        desc = _CATEGORY_DESCRIPTIONS.get(
            cat,
            f"terms characteristic of {cat} discourse in the domain.",
        )
        bullets.append(f"- **{cat}**: {desc}")
    bullets_text = "\n\n".join(bullets)
    keys_json = ", ".join(f'"{c}"' for c in taxonomy)
    output_example = (
        "{\n"
        + "\n".join(f'  "{c}": ["term1", "term2", ...],' for c in taxonomy)
        + "\n}"
    )
    return (
        "You are classifying vocabulary terms for a multi-domain RAG retrieval system.\n"
        "Given a scope name, its description, and a list of IDF-scored terms extracted\n"
        f"from that scope's corpus, classify each term into one of these categories:\n\n"
        f"{bullets_text}\n\n"
        "Rules:\n"
        "1. A term may appear in only one category (choose the best fit).\n"
        "2. DROP noise — these are never useful vocabulary:\n"
        "   - Single letters or bare symbols (a, r, x, θ)\n"
        "   - Document structure references (theorem 4.1, lemma a.1, figure 4, table 2)\n"
        "   - Author citation fragments (et al., guijarro-ordonez et al. (2021))\n"
        "   - Mathematical variable names without semantic meaning (z[q], θ[q])\n"
        "   - Overly generic words (model, system, data, method, results, approach)\n"
        "3. Use the scope description to guide domain-appropriate classification.\n"
        "   The same term can belong to different categories in different domains.\n"
        "4. You may add 2-4 additional high-value terms per category that are\n"
        "   obviously missing but central to the scope. Mark these with a trailing\n"
        "   asterisk (*) so the caller knows they were inferred.\n"
        "5. Return valid JSON only.\n\n"
        f"Output format (keys: {keys_json}):\n"
        f"{output_example}\n"
    )


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
    taxonomy: list[str] | None = None,
    chat_url: str = DEFAULT_STARGATE_CHAT_URL,
    client: httpx.AsyncClient | None = None,
) -> dict[str, list[str]] | None:
    """Classify terms via Stargate chat completions (async).

    taxonomy: ordered list of category names to classify into. Defaults to
    DEFAULT_TAXONOMY when omitted. Pass config.vocabulary_taxonomy so that
    custom categories (e.g. 'quantitative') are included in the prompt and
    parsed from the response.
    """
    effective_taxonomy = taxonomy if taxonomy is not None else DEFAULT_TAXONOMY
    keys_str = ", ".join(effective_taxonomy)
    user_msg = (
        f"Scope: {scope}\n"
        f"Description: {description}\n"
        f"Terms to classify:\n{json.dumps(terms)}\n\n"
        f"Return JSON with keys: {keys_str}."
    )
    prompt = build_classification_prompt(effective_taxonomy)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
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
            cat: [
                str(t) for t in parsed.get(cat, []) if isinstance(t, str) and t.strip()
            ]
            for cat in effective_taxonomy
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


def _resolve_scope_vocab_mode(scope_name: str, config: RagConfig) -> str:
    """Return effective vocab mode for a scope: per-scope override or global default."""
    sdef = config.scopes.get(scope_name)
    if sdef is not None and sdef.vocab_mode:
        return sdef.vocab_mode
    return config.vocabulary_mode or "local"


async def _classify_frontier_scopes(
    scope_names: list[str],
    chat_url: str,
) -> set[str]:
    """Classify scopes via vocab-classify-v1 pipeline (frontier/cloud models).

    The pipeline writes vocabulary to the property index itself; the caller is
    responsible for stamping watermarks. Returns the set of scopes written.
    """
    payload: dict = {
        "model": "vocab-classify-v1",
        "messages": [{"role": "user", "content": "vocabulary classification"}],
        "pipeline_options": {
            "mode": "frontier",
            "scopes": scope_names,
            "skip_fresh": False,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0)) as client:
            resp = await client.post(chat_url, json=payload)
            resp.raise_for_status()
        choices = resp.json().get("choices", [])
        content = choices[0]["message"]["content"] if choices else "{}"
        vocab = json.loads(content).get("vocabulary", {})
        return set(vocab.keys())
    except Exception as exc:
        logger.error(
            "Frontier vocab classification failed for %d scope(s): %s",
            len(scope_names),
            exc,
        )
        return set()


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

    Per-scope vocab_mode (set in rag.yaml under each scope) overrides the global
    vocabulary_mode. Scopes with vocab_mode=frontier use vocab-classify-v1;
    scopes with vocab_mode=none are skipped (corpus hints still refresh);
    all others use the lightweight local-model path.
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

        # Only classify scopes that have no existing vocabulary candidates
        # and are not explicitly opting out of classification (vocab_mode=none).
        # Corpus hints are always refreshed above; classification is expensive
        # and runs once per scope — until vocabulary is explicitly cleared.
        classify_scopes = [
            s
            for s in set(stale_scopes) & set(cs_map.keys())
            if not property_index.has_scope_vocabulary(s)
            and _resolve_scope_vocab_mode(s, config) != "none"
        ]

        local_scopes = [
            s
            for s in classify_scopes
            if _resolve_scope_vocab_mode(s, config) == "local"
        ]
        frontier_scopes = [
            s
            for s in classify_scopes
            if _resolve_scope_vocab_mode(s, config) == "frontier"
        ]

        if model is None and local_scopes:
            for scope_name in local_scopes:
                no_model_scopes.append(scope_name)
                logger.warning(
                    "Scope %s: hints refreshed but no Stargate model — "
                    "vocabulary skipped (stale until model available)",
                    scope_name,
                )
        elif local_scopes:
            from services.rag.corpus_hints import (
                compute_scope_files_hash,
                load_corpus_hints,
            )

            hints_map = load_corpus_hints()
            descriptions = {
                n: getattr(sdef, "description", "") or ""
                for n, sdef in config.scopes.items()
            }

            for scope_name in sorted(local_scopes):
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
                        taxonomy=config.vocabulary_taxonomy,
                        chat_url=chat_url,
                        client=client,
                    )
                except Exception as e:
                    logger.warning(
                        "Per-scope classify failed for %s: %s", scope_name, e
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

        if frontier_scopes:
            written = await _classify_frontier_scopes(
                sorted(frontier_scopes), chat_url=chat_url
            )
            vocab_ok.extend(s for s in frontier_scopes if s in written)
            vocab_failed.extend(s for s in frontier_scopes if s not in written)
            if written:
                logger.info(
                    "Frontier vocab classification completed for %d scope(s): %s",
                    len(written),
                    sorted(written),
                )
            failed = sorted(set(frontier_scopes) - written)
            if failed:
                logger.warning(
                    "Frontier vocab classification failed for %d scope(s): %s",
                    len(failed),
                    failed,
                )

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
    "DEFAULT_STARGATE_CHAT_URL",
    "DEFAULT_STARGATE_MODELS_URL",
    "DEFAULT_TAXONOMY",
    "build_classification_prompt",
    "classify_scope_async",
    "configured_scopes_map",
    "pick_loaded_stargate_model",
    "run_scope_freshness_repair",
]
