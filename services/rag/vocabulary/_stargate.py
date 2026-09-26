"""Stargate endpoint URL constants for vocabulary classification requests.

``DEFAULT_STARGATE_CHAT_URL`` points at the local gateway's OpenAI-compatible
chat completions route. It is the default ``chat_url`` for
``classify_scope_async`` and ``run_scope_freshness_repair``, and is imported by
``scripts/rag/classify_vocabulary.py`` for pipeline calls.
"""

from __future__ import annotations

DEFAULT_STARGATE_CHAT_URL = "http://localhost:9999/v1/chat/completions"
