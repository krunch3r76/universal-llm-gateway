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

from ._categories import DEFAULT_TAXONOMY
from ._classify import classify_scope_async
from ._prompt import build_classification_prompt
from ._repair import run_scope_freshness_repair
from ._scope_helpers import _resolve_scope_vocab_mode, configured_scopes_map
from ._stargate import DEFAULT_STARGATE_CHAT_URL

__all__ = [
    "DEFAULT_STARGATE_CHAT_URL",
    "DEFAULT_TAXONOMY",
    "_resolve_scope_vocab_mode",
    "build_classification_prompt",
    "classify_scope_async",
    "configured_scopes_map",
    "run_scope_freshness_repair",
]
