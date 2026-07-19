"""Embedding client tunables and scope instruction templates."""

from __future__ import annotations

GATEWAY_URL = "http://localhost:9999"

PROBE_INTERVAL_S = 2.0
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
