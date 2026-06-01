"""Registered MCP tools intentionally absent from canonical.yaml (dispatch-only overflow).

∀ name ∈ INTENTIONAL_OVERFLOW: registered @mcp.tool in code, not a flat/dispatcher
tool name in canonical.yaml — deliberate overflow visibility. New first-class tools
(git_land class) must be canonicalized, not added here.

To promote a tool: remove from this set AND add a canonical.yaml entry.
"""

from __future__ import annotations

INTENTIONAL_OVERFLOW: frozenset[str] = frozenset(
    {
        # Legacy file ops superseded by fs_* canonical flat shapes
        "copy_file",
        "copy_project_file",
        "delete_file",
        "delete_project_file",
        "edit_file",
        "edit_project_file",
        "files",
        "list_files",
        "list_project_files",
        "move_file",
        "move_project_file",
        "read_file",
        "read_project_file",
        "remove_directory",
        "search_project_files",
        "write_file",
        "write_project_file",
        # Context ops superseded by fs workspaces sandbox
        "delete_context_file",
        "edit_context_file",
        "list_context_directory",
        "move_context_file",
        "read_context_file",
        "write_context_file",
        # Journal ops superseded by cortex_* named tools
        "list_journal_entries",
        "read_journal_entry",
        "session_close",
        "session_store",
        "write_journal_entry",
        # Browser and imaging tools — UI-specific; no canonical promotion path
        "browse",
        "browser_click",
        "browser_fill",
        "browser_get_content",
        "browser_get_structure",
        "browser_load_cookies",
        "browser_navigate",
        "browser_refresh_session",
        "browser_screenshot",
        "google_imagine",
        "grok_imagine",
        "openai_imagine",
        "view_image",
        # Cortex extended ops not yet promoted to canonical flat shapes
        "cortex_boot",
        "cortex_chunk_create",
        "cortex_chunk_get",
        "cortex_staging_list",
        "cortex_staging_reject",
        "cortex_surface_form_create",
        "cortex_surface_form_lookup",
        # Private / local tools (tools.local/ gitignored layer — absent in CI)
        "bot_supervisor",
        "claudeburst",
        "email",
        "extract_document_structured",
        "finance",
        "ingest_binary",
        "local_api",
        "usps_track",
        # Domain tools not yet canonicalized
        "advisor",
        "agent_consult",
        "boot_inspect",
        "extract_directory",
        "extract_document",
        "js_analyze",
        "llm_generate",
        "markdown",
        "model_status",
        "promote_document_to_evidence",
        "topology",
        # Web / HTTP tools not yet canonicalized
        "http_diff",
        "http_replay",
        "http_request",
        "web_fetch",
        "web_search",
        # RAG extended ops beyond canonical rag_* flat shapes
        "rag_answer",
        "rag_delete_directory",
        "rag_delete_source",
        "rag_get_chunks",
        "rag_list_articles",
        "rag_list_scopes",
        "rag_orphaned_articles",
        "rag_refresh_corpus_hints",
        "rag_search_preview",
        "rag_source_status",
        # Observability / quality extras not yet in canonical
        "quality_gate",
        "query_observability_preview",
        # SQLite tools not yet canonicalized
        "sql",
        "sqlite_execute",
        "sqlite_list_databases",
        "sqlite_schema",
        # Dispatch tools registered under old names
        # (canonical uses dispatch_frontier / dispatch_team flat shapes)
        "frontier_dispatch",
        "team_dispatch",
        # MCP server heartbeat — infra-internal, not a domain tool
        "health",
        # Models listing tool not yet canonicalized
        "list_models",
        # Archived harness stub — canonical entries removed (assertion 11588)
        "grokbuild",
    }
)
