"""Embedding client tunables and scope instruction templates.

Holds the gateway URL, batch size and token-estimate ratio, retry/backoff and
model rewarm timings, transient HTTP status codes, and the per-scope
``SCOPE_INSTRUCTIONS`` used to build ``Instruct:`` query prefixes. Imported by
the ``services.rag.embeddings`` submodules (batch_post, chunk_embed, health,
model_id, query_embed); values are module constants, not runtime config.
"""

from __future__ import annotations

from services.rag.config._models import DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S

GATEWAY_URL = "http://localhost:9999"

PROBE_TIMEOUT_S = 120.0

EMBED_BATCH_SIZE = 16
CHARS_PER_TOKEN = 3
N_CTX_HEADROOM = 0.85
FALLBACK_MAX_BATCH_TOKENS = 7000

EMBED_RETRY_ATTEMPTS = 3
EMBED_RETRY_BACKOFF_S = 1.0

REWARM_TIMEOUT_S = 360.0
REWARM_PROBE_TIMEOUT_S = 30.0
REWARM_POLL_INTERVAL_S = 10.0

QUERY_RETRY_ATTEMPTS = 4
QUERY_RETRY_BASE_S = 0.25
QUERY_RETRY_MAX_S = 3.0

# Stargate X-Request-Timeout budgets (seconds). Batch floor = starvation drain 15+30.
BATCH_EMBED_REQUEST_TIMEOUT_S = 45.0
# Overridden at startup from RagConfig.query_embed_request_timeout_s.
QUERY_EMBED_REQUEST_TIMEOUT_S = DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S
QUERY_CPU_FAILOVER_ENABLED = False
BATCH_CPU_FAILOVER_ENABLED = False

TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})

SCOPE_INSTRUCTIONS: dict[str, str] = {
    "project": "Find relevant architecture documentation about this topic",
    "research": "Find relevant research papers and technical analysis about this topic",
    "prompting": "Find relevant prompt engineering techniques and patterns",
    "workflows": "Find relevant pipeline orchestration and agent coordination patterns",
    "llm_foundations": "Find relevant LLM reference material about this topic",
    "code_retrieval": "Find relevant code retrieval research about this topic",
    "both": "Find relevant documentation or research about this topic",
    "all": "Find relevant information about this topic",
}
DEFAULT_INSTRUCTION = "Find relevant information about this topic"
