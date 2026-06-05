"""Prompt battery for P7 probe — keyword-match dispatcher selection accuracy.

Each entry: (prompt_text, expected_domain, notes). Near-neighbor prompts
are marked with [NN] in notes. Kept here so the probe's main module stays
under SLOC budget; the data IS the test surface.
"""

from __future__ import annotations

_STOP = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might must to of in on for from with "
    "at by this that these those it its not and or but so if when then "
    "all any some only each per no".split()
)

_PROMPT_BATTERY: list[tuple[str, str, str]] = [
    # agent_bus (5 ops: fetch, get, post, reply, threads)
    ("list recent agent-bus threads", "agent_bus", ""),
    ("reply to agent-bus thread 480 with my verdict", "agent_bus", "[NN: cortex]"),
    # cortex (18 ops: assert, search, entity_get, journal_read, session_close, ...)
    ("save this observation as a cortex assertion", "cortex", ""),
    ("search cortex for entities related to dual-seat routing", "cortex", ""),
    ("read the activity journal from this session", "cortex", "[NN: agent_bus]"),
    # dispatch (overflow op only; consults use team_dispatch primary)
    ("dispatch an overflow tool by name", "dispatch", ""),
    ("run a team dispatch to reviewer role", "team_dispatch", "[NN: pipeline]"),
    # fs (16 ops: read, write, append, list, delete, ...)
    ("read the file at workspaces/universal-llm-gateway/docs/VISION.md", "fs", ""),
    ("write a markdown file to the cortex sandbox", "fs", ""),
    # grokbuild (6 ops: build, build_status, fetch_result, worktree_create, ...)
    ("create a grokbuild worktree for the feature branch", "grokbuild", ""),
    # manage (5 ops: health, rebuild, restart, status, wait_healthy)
    ("rebuild and restart the mcp service", "manage", ""),
    ("check if stargate is healthy and wait until it is", "manage", ""),
    # observability (3 ops: preview, query, recent_failures)
    ("show me recent errors and failures from the event log", "observability", ""),
    # pipeline (3 ops: consult, iterate, run)
    ("run the transcript pipeline on this input", "pipeline", "[NN: dispatch]"),
    # rag (4 ops: search, coverage, list_sources, upsert_article)
    ("search for RAG articles about LLM routing architectures", "rag", ""),
    # retrieve (1 op: payload)
    ("retrieve the stored response rs_abc123", "retrieve", ""),
    # tool_search (1 op: query)
    ("search for overflow tools that handle audio transcription", "tool_search", ""),
    # Near-neighbor edge cases
    (
        "find research articles about multi-agent coordination",
        "rag",
        "[NN: cortex, tool_search]",
    ),
    (
        "query the event service for recent pipeline failures",
        "observability",
        "[NN: pipeline]",
    ),
]


__all__ = ["_PROMPT_BATTERY", "_STOP"]
