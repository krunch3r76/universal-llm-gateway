"""MCP server — Streamable HTTP transport with bearer token and OAuth 2.1 auth.

Internet-facing service at :443 (TLS). Exposes filesystem tools to
Anthropic models via the mcp_servers API parameter.

Security boundaries:
  - Auth admission via static bearer token or OAuth 2.1 (PKCE + S256)
  - TLS via Let's Encrypt certs mounted at /etc/letsencrypt (read-only)
  - Filesystem access sandboxed to /data/files via volume mount
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from typing import TYPE_CHECKING, Any, override

import uvicorn
from auth_middleware import AuthMiddleware
from fastmcp import FastMCP
from mcp_events import monotonic_now, record
from mcp_request_middleware import McpRequestEventsMiddleware
from mcp_toolprogress import toolprogress_begin, toolprogress_end
from oauth_config import OAuthServerConfig, load_oauth_config
from oauth_routes import build_oauth_routes
from oauth_service import OAuthService
from oauth_store import OAuthStore
from request_profile import current_profile
from response_size_guard import register_response_guard
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from tool_access import dispatch_denial_reason, is_dispatch_tool_allowed
from tools.agent_bus import register_agent_bus_tools
from tools.agent_consult import register_agent_consult_tools
from tools.browser import register_browser_tools
from tools.context import register_context_tools
from tools.cortex import register_cortex_tools
from tools.cortex_v2 import register_cortex_v2_tools
from tools.document_ocr import register_document_ocr_tools
from tools.events import register_event_tools
from tools.filesystem import register_filesystem_tools
from tools.finance import register_finance_tools
from tools.finance_ingest import register_finance_ingest_tools
from tools.finance_reconcile import register_finance_reconcile_tools
from tools.finance_smart_ingest import register_finance_smart_ingest_tools
from tools.frontier import register_frontier_tools
from tools.ingest_binary import register_ingest_binary_tools
from tools.llm import register_llm_tools
from tools.local_api import register_local_api_tools
from tools.manage import register_manage_tools
from tools.markdown_tool import register_markdown_tools
from tools.model_status import register_model_status_tools
from tools.pipeline import register_pipeline_tools
from tools.pipeline_consult import register_pipeline_consult_tools
from tools.project import register_project_tools
from tools.quality import register_quality_tools
from tools.rag import register_rag_tools
from tools.rag_articles import register_rag_article_tools
from tools.sqlite import register_sqlite_tools
from tools.web import register_web_tools

if TYPE_CHECKING:
    from asyncio.transports import BaseTransport
    from collections.abc import Callable

    from starlette.types import Send

logger = logging.getLogger(__name__)

_AUTH_TOKEN_ENV = "MCP_AUTH_TOKEN"
_CERT_FILE = "/etc/letsencrypt/live/mcp.k-1.me/fullchain.pem"
_KEY_FILE = "/etc/letsencrypt/live/mcp.k-1.me/privkey.pem"
_HOST = "0.0.0.0"
_PORT = 443
_TCP_KEEPIDLE = 10
_TCP_KEEPINTVL = 10
_TCP_KEEPCNT = 3
_SSE_PING_INTERVAL: int = int(os.getenv("MCP_SSE_PING_INTERVAL", "15"))


def _env_truthy(name: str, default: bool) -> bool:
    """Return True if env var is set to a truthy value ('1', 'true', 'yes', 'on'), else default.

    Returns:
        True if the env var is truthy; otherwise the default.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _patch_sse_ping() -> None:
    """Patch SSE response construction to emit heartbeat events at a fixed cadence.

    The patch injects a named `heartbeat` event and a configurable ping interval
    during `EventSourceResponse` initialization so idle transports stay active.
    """
    _orig_init = EventSourceResponse.__init__

    def _patched_init(
        self: EventSourceResponse, *args: object, **kwargs: object
    ) -> None:
        kwargs["ping_message_factory"] = lambda: ServerSentEvent(
            event="heartbeat", data="{}"
        )
        kwargs["ping"] = _SSE_PING_INTERVAL
        _orig_init(self, *args, **kwargs)

    # Monkey-patch __init__ to inject custom ping interval via kwargs.
    # This is a type-unsafe operation due to diverging signatures. Consider subclassing
    # EventSourceResponse if a more type-safe approach is desired.
    EventSourceResponse.__init__ = _patched_init  # type: ignore[method-assign]


def _patch_sse_lifecycle_events() -> None:
    """Wrap SSE stream execution with structured lifecycle event emission.

    Emits `mcp.sse.stream.started`, `mcp.sse.stream.aborted`, and
    `mcp.sse.stream.ended` so stream health is visible in event telemetry.
    """
    # Accessing protected member for lifecycle-event monkey-patch. This is brittle.
    _orig_stream = EventSourceResponse._stream_response  # type: ignore[attr-defined]

    async def _stream_with_events(self: EventSourceResponse, send: Send) -> None:
        t0 = monotonic_now()
        record("mcp.sse.stream.started")
        record(
            "mcp.transport.sse.session.started",
            transport="https",
            channel="sse",
        )
        try:
            await _orig_stream(self, send)
        except Exception as exc:
            duration = monotonic_now() - t0
            record(
                "mcp.sse.stream.aborted",
                duration_s=round(duration, 3),
                reason=str(exc),
                exc_type=type(exc).__name__,
            )
            record(
                "mcp.transport.sse.session.aborted",
                transport="https",
                channel="sse",
                duration_s=round(duration, 3),
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            logger.warning(
                "SSE stream aborted after %.1fs: %s",
                duration,
                exc,
                exc_info=True,
            )
            raise
        else:
            duration = monotonic_now() - t0
            record(
                "mcp.sse.stream.ended",
                duration_s=round(duration, 3),
                reason="clean",
            )
            record(
                "mcp.transport.sse.session.ended",
                transport="https",
                channel="sse",
                duration_s=round(duration, 3),
            )
            # Only log streams that did real work; sub-100ms = ListTools handshake.
            if duration >= 0.1:
                logger.info("SSE stream ended cleanly after %.1fs", duration)

    # Monkey-patch protected method to wrap stream with start/end events. This is brittle.
    EventSourceResponse._stream_response = _stream_with_events  # type: ignore[method-assign]


_patch_sse_ping()
_patch_sse_lifecycle_events()


def _require_env(name: str) -> str:
    """Return the value of *name* from the environment, exiting if unset or empty.

    Args:
        name: Environment variable name.

    Returns:
        Stripped value.

    Raises:
        SystemExit: If the variable is unset or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("Required environment variable %s is not set", name)
        sys.exit(1)
    return value


def _set_tcp_keepalive(sock: socket.socket) -> None:
    """Enable TCP keepalive with short probe intervals.

    Prevents NAT/firewall connection tracking from evicting idle TCP
    sessions during long model-thinking pauses between MCP tool calls.

    Args:
        sock: The socket to configure.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, _TCP_KEEPIDLE)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, _TCP_KEEPINTVL)
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, _TCP_KEEPCNT)


_OAUTH_DB_PATH = "/data/cortex/oauth.db"


def _build_oauth_service(config: OAuthServerConfig | None) -> OAuthService | None:
    """Construct an OAuthService from config, or None if OAuth is disabled."""
    if config is None:
        return None
    store = OAuthStore(db_path=_OAUTH_DB_PATH)
    return OAuthService(config=config, store=store)


_PRIMARY_TOOLS: set[str] = {
    # Meta
    "dispatch",
    "web_search",
    # Consolidated file surface
    "fs",
    # SQLite
    "sql",
    # Infra
    "pipeline",
    "manage",
    "model_status",
    "quality_gate",
    "observability",
    # Agent-bus (dispatch-style)
    "agent_bus",
    # Cortex (dispatch-style + boot)
    "cortex",
    "cortex_boot",
    # LLM
    "frontier_generate",
    # RAG (consolidated)
    "rag",
    # Response size guard
    "retrieve",
}


def _build_server() -> FastMCP:
    """Construct the FastMCP server, register tool surfaces, and prune exports.

    The resulting server advertises a primary tool set and routes non-primary
    tools through `dispatch` for clients with limited tool enumeration capacity.
    """
    mcp: FastMCP = FastMCP("gateway-tools")
    register_filesystem_tools(mcp)
    register_markdown_tools(mcp)
    register_manage_tools(mcp)
    register_model_status_tools(mcp)
    register_project_tools(mcp)
    register_web_tools(mcp)
    register_rag_tools(mcp)
    register_rag_article_tools(mcp)
    tool_configs = {
        "ENABLE_CONTEXT_TOOLS": (register_context_tools, True, "Context tools"),
        "ENABLE_BROWSER_TOOLS": (register_browser_tools, False, "Browser tools"),
    }

    for env_var, (register_fn, default_enabled, tool_name) in tool_configs.items():
        if _env_truthy(env_var, default=default_enabled):
            register_fn(mcp)
        else:
            logger.info(f"{tool_name} disabled ({env_var}=false)")
    register_sqlite_tools(mcp)
    register_event_tools(mcp)
    register_finance_tools(mcp)
    register_finance_ingest_tools(mcp)
    register_finance_reconcile_tools(mcp)
    register_finance_smart_ingest_tools(mcp)
    register_ingest_binary_tools(mcp)
    register_document_ocr_tools(mcp)
    register_pipeline_tools(mcp)
    register_pipeline_consult_tools(mcp)
    register_quality_tools(mcp)
    register_local_api_tools(mcp)
    register_agent_bus_tools(mcp)
    register_agent_consult_tools(mcp)
    register_cortex_tools(mcp)
    register_cortex_v2_tools(mcp)
    register_llm_tools(mcp)
    register_frontier_tools(mcp)

    @mcp.tool()
    def health() -> dict[str, str]:
        """Health check — confirms the MCP server is reachable."""
        return {"status": "ok"}

    try:
        register_response_guard(mcp)
    except Exception:
        logger.exception(
            "Failed to initialize response size guard — proceeding without it"
        )
        record("mcp.response.guard.init_failed", error="see server logs")

    overflow_registry: dict[str, Callable[..., Any]] = _prune_to_primary(mcp)

    sandbox_tool: dict[str, str] = {
        "files": "files",
        "context": "context",
        "project": "project",
    }
    md_op_map: dict[str, str] = {
        "md_list": "list_sections",
        "md_read": "read_section",
        "md_replace": "replace_section",
        "md_append": "append_section",
        "md_delete": "delete_section",
    }

    @mcp.tool()
    def fs(
        op: str,
        sandbox: str,
        path: str = "",
        paths: list[str] | None = None,
        content: str = "",
        target: str = "",
        line: int = 0,
        section: str = "",
        all_occurrences: bool = False,
        include_untracked: bool = True,
    ) -> dict[str, Any]:
        """Unified file operations across all sandboxes.

        sandbox: "files"   = /data/files (documents, notes, uploads)
                 "context" = tasks/ (specs, workspace scratchpads)
                 "project" = repo root (source code, configs)

        Both sandbox and op are REQUIRED.

        Ops (standard):
          read           — path required
          read_multi     — paths required (array)
          write          — path, content required
          append         — path, content required
          prepend        — path, content required
          replace        — path, target required; content = replacement; all_occurrences?
          insert_at_line — path, content, line required
          list           — path optional (defaults to sandbox root)
          delete         — path required
          search         — project sandbox only; path = dir, content = regex pattern

        Ops (markdown sections — for large docs >5k chars):
          md_list    — list sections (path required)
          md_read    — read section (path, section required)
          md_replace — replace section (path, section, content required)
          md_append  — append to section (path, section, content required)
          md_delete  — delete section (path, section required)
        """
        if not op:
            return {"error": "'op' is required"}
        if sandbox not in sandbox_tool:
            return {
                "error": f"sandbox must be 'files', 'context', or 'project', got {sandbox!r}"
            }

        if op.startswith("md_"):
            md_fn = overflow_registry.get("markdown")
            if md_fn is None:
                return {"error": "markdown tool not available"}
            md_op = md_op_map.get(op)
            if md_op is None:
                valid = ", ".join(sorted(md_op_map))
                return {"error": f"Unknown markdown op: {op!r}. Available: {valid}"}
            return md_fn(
                op=md_op, path=path, sandbox=sandbox, section=section, content=content
            )

        tool_name = sandbox_tool[sandbox]
        fn = overflow_registry.get(tool_name)
        if fn is None:
            return {"error": f"{tool_name} tool not available"}

        if sandbox == "project":
            return fn(
                op=op,
                path=path,
                content=content,
                target_str=target,
                line=line,
                all_occurrences=all_occurrences,
                include_untracked=include_untracked,
            )
        if sandbox == "files":
            if op == "read_multi":
                paths = paths or []
                return fn(op=op, path=path, paths=paths, content=content, target=target)
            return fn(
                op=op,
                path=path,
                content=content,
                target=target,
                line=line,
                all_occurrences=all_occurrences,
            )
        return fn(
            op=op,
            path=path,
            content=content,
            target=target,
            line=line,
            all_occurrences=all_occurrences,
        )

    rag_op_tool: dict[str, str] = {
        "search": "rag_search",
        "answer": "rag_answer",
        "list_scopes": "rag_list_scopes",
        "coverage": "rag_coverage",
        "upsert_article": "rag_upsert_article",
        "delete_source": "rag_delete_source",
        "refresh_hints": "rag_refresh_corpus_hints",
        "orphaned_articles": "rag_orphaned_articles",
        "delete_directory": "rag_delete_directory",
    }

    @mcp.tool()
    async def rag(op: str, arguments: str = "{}") -> Any:
        """RAG knowledge retrieval and index management.

        Ops:
          search            — query REQUIRED, scope?, prefix?, top_k?
          answer            — question REQUIRED, scope?, prefix?, deep?
          list_scopes       — no args
          coverage          — no args
          upsert_article    — url REQUIRED, title?, scope?
          delete_source     — source_hash REQUIRED
          refresh_hints     — scope?
          orphaned_articles — no args
          delete_directory  — directory REQUIRED

        scope and prefix are mutually exclusive.
        """
        import json as _json

        tool_name = rag_op_tool.get(op)
        if tool_name is None:
            valid = ", ".join(sorted(rag_op_tool))
            return {"error": f"Unknown rag op: {op!r}. Available: {valid}"}

        profile = current_profile()
        if not is_dispatch_tool_allowed(profile, tool_name):
            reason = dispatch_denial_reason(tool_name)
            record(
                "mcp.profile.tool.denied",
                profile=profile,
                tool=tool_name,
                entrypoint="rag",
                reason=reason,
            )
            return {"error": reason}

        fn = overflow_registry.get(tool_name)
        if fn is None:
            return {"error": f"RAG tool {tool_name!r} not available"}

        t_prog, prog_timer = toolprogress_begin("rag", op=op)
        err: str | None = None
        try:
            try:
                args = _json.loads(arguments)
                if not isinstance(args, dict):
                    return {
                        "error": f"arguments must be a JSON object, got {type(args).__name__}"
                    }
            except _json.JSONDecodeError as exc:
                return {"error": f"Invalid arguments JSON: {exc}"}

            result = fn(**args)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(t_prog, prog_timer, "rag", error=err, op=op)

    @mcp.tool()
    async def dispatch(tool: str, arguments: str = "{}") -> Any:
        """Call any server tool by name — gateway to tools beyond the primary set.

        Some MCP clients enumerate only a limited number of tools. Use dispatch
        to reach any tool not in your direct list.

        Dispatchable tools:
          Sandboxed files (individual — prefer primary `fs` tool):
            read_file(path) — read file
            write_file(path, content) — write/create file
            edit_file(path, operation, content, ...) — edit file
            list_files(directory?) — list sandboxed files
            delete_file(path) — delete sandboxed file
          File utilities:
            view_image(path, max_dimension?, quality?, mode?) — view photo/screenshot
                mode: "copy" (default) returns a shared local image path; "image" returns inline ImageContent
            move_file(source, destination) — move/rename any file
            copy_file(source, destination) — copy any file
            remove_directory(directory) — delete directory and contents
            ingest_binary(path, content_base64, media_type?, entity_id?, entity_name?, entity_description?)
                Store binary evidence under /data/files/evidence and create the
                matching Cortex document entity. Use when an agent already has
                binary bytes and needs a first-class evidence artifact.
          Context files (individual — prefer primary `fs` tool with sandbox="context"):
            read_context_file(path) — read context file
            write_context_file(path, content) — write context file
            edit_context_file(path, operation, content, ...) — edit context file
            delete_context_file(path) — delete context file
            list_context_directory(path?) — list context directory
          Project files (individual — prefer primary `fs` tool with sandbox="project"):
            read_project_file(path) — read project file
            write_project_file(path, content) — write project file
            edit_project_file(path, operation, content, ...) — edit project file
            list_project_files(directory?, max_depth?) — list project files
            search_project_files(pattern, directory?, max_results?) — search code
          Search & knowledge:
            rag_search(query, scope?, prefix?, top_k?) — semantic search.
                scope: named scope or list (e.g. "research", ["rag_systems","workflows"]).
                prefix: absolute source-path prefix or list for ad-hoc filtering
                    (e.g. "/mnt/torus/projects/.../docs/research/rag-systems").
                scope and prefix are mutually exclusive. top_k default 20.
            rag_answer(question, scope?, prefix?, deep?) — RAG-grounded answer.
                scope/prefix same as rag_search. deep=True for iterative retrieval.
            rag_list_scopes() — list available scopes with prefixes and coverage
            rag_coverage() — per-scope, per-prefix indexed file counts
            rag_upsert_article(url, title?, scope?) — index article
            rag_delete_source(source_hash) — delete indexed source
            rag_refresh_corpus_hints(scope?) — regenerate discriminative vocabulary hints
            rag_orphaned_articles() — find articles not in any scope
            rag_delete_directory(directory) — delete all indexed content under a path
          Web:
            web_fetch(url) — fetch URL content
          Database:
            sqlite_execute(db, statement, params?) — execute SQL write
            sqlite_schema(db?) — show table schemas
            sqlite_list_databases() — list configured DBs
          LLM generation:
            llm_generate(messages, system?, model?, max_tokens?) — generate text
                via native cloud API (Anthropic / xAI / OpenAI) with server-side
                credentials; returns {content, model, usage, provider}
          Finance:
            finance_extract_pdf(path) — extract tables + text from a PDF via pdfplumber.
                Returns per-page tables (column-aligned) and full text. Best for bank/CC statements.
            finance_extract_directory(directory) — batch extract all PDFs in a directory.
                Runs finance_extract_pdf on each .pdf found; returns array of results.
            finance_parse_statement(path, statement_type) — parse a financial PDF into
                structured JSON via Claude API. statement_type: checking, credit_card,
                utility, phone, ploc, student_loan, brokerage, tax_document, property_tax.
            finance_parse_directory(directory, type_map) — batch-parse all PDFs in a
                directory. type_map maps filename patterns to statement types.
            finance_ingest_statement(parsed_json?, path?, statement_type?) — ingest a
                parsed financial statement into Cortex. End-to-end mode (path + type)
                runs Phase 2 parser then ingests. Direct mode (parsed_json) skips parsing.
                Creates account/tax/property entities + temporally scoped assertions.
            finance_ingest_directory(directory, type_map) — batch ingest all PDFs via
                end-to-end pipeline. One-command monthly ingestion into Cortex.
          Document OCR:
            document_ocr(path, prompt?, pages?, dpi?, model?) — OCR a scanned PDF or
                image via Claude Vision. Use when pdfplumber returns empty/garbage.
            document_ocr_structured(path, statement_type, dpi?, model?) — OCR + structured
                extraction combo. Renders scanned pages, sends with schema prompt, returns
                JSON compatible with finance_ingest_statement.
            document_ocr_directory(directory, prompt?, dpi?, model?) — batch OCR all
                PDFs and images in a directory.
          Quality & infra:
            quality_gate(files) — run ruff + compileall
            pipeline_consult(execution_id, step_name, problem)
            validate_pipeline(path)
            health() — server health check
          Observability:
            observability(operation, params?) — event queries (also primary)
          Internal services:
            local_api(service, method, path, body?, token?) — relay to Docker services
          Cortex (dispatch-only — primary ops use cortex(tool=...) directly):
            cortex_chunk_create(content, source_uri?, ...) — create source chunk
            cortex_chunk_get(chunk_id) — get chunk by ID
            cortex_surface_form_create(mention, entity_id, chunk_id, ...) — resolved mention
            cortex_surface_form_lookup(mention, context_hash) — resolution cache lookup
            cortex_staging_list(status?, source_uri?, limit?) — list staging proposals
            cortex_staging_reject(staging_id, reviewer?) — reject staging proposal
          Journal:
            list_journal_entries() — list recent entries
            read_journal_entry(id) — read entry
            write_journal_entry(title, content, tags?)
            list_clips() — list saved clips
            read_clip(name) — read a clip
          Browser (if enabled):
            browser_navigate, browser_click, browser_fill,
            browser_screenshot, browser_get_structure, browser_get_content

        Example:
            dispatch(tool="quality_gate", arguments='{"files": ["server.py"]}')
            dispatch(tool="read_file", arguments='{"path": "notes.md"}')

        Args:
            tool: Name of the tool to invoke.
            arguments: JSON string of tool arguments (default "{}").

        Returns:
            Native MCP content for passthrough tool outputs, otherwise
            {"tool": "<name>", "result": <tool output as dict/list>}.
        """
        import json as _json

        profile = current_profile()
        if not is_dispatch_tool_allowed(profile, tool):
            reason = dispatch_denial_reason(tool)
            record(
                "mcp.profile.tool.denied",
                profile=profile,
                tool=tool,
                entrypoint="dispatch",
                reason=reason,
            )
            return {"tool": tool, "result": {"error": reason}}

        fn = overflow_registry.get(tool)
        if fn is None:
            raise ValueError(
                f"Unknown dispatch tool: {tool!r}. "
                f"Available: {sorted(overflow_registry)}"
            )
        parsed = _json.loads(arguments)
        record(
            "mcp.profile.dispatch.routed",
            profile=profile,
            tool=tool,
        )
        result = fn(**parsed)
        if asyncio.iscoroutine(result):
            result = await result
        record("mcp.tool.dispatch.success", tool=tool)
        if hasattr(result, "model_dump"):
            return result
        return {"tool": tool, "result": result}

    primary_count = len(_PRIMARY_TOOLS)
    logger.info(
        "Tool pruning: %d primary (advertised), %d overflow (via dispatch)",
        primary_count,
        len(overflow_registry),
    )
    return mcp


def _prune_to_primary(mcp: FastMCP) -> dict[str, Callable[..., Any]]:
    """Remove non-primary tools from the exported MCP catalog.

    Returns a registry of removed callables so `dispatch` can still invoke
    them by name while keeping the advertised tool list intentionally compact.
    """
    import asyncio

    async def _collect() -> dict[str, Callable[..., Any]]:
        registry: dict[str, Callable[..., Any]] = {}
        all_tools = await mcp.list_tools()
        for t in all_tools:
            if t.name not in _PRIMARY_TOOLS:
                tool_obj = await mcp.get_tool(t.name)
                registry[t.name] = tool_obj.fn
        return registry

    # asyncio.run is used here during application startup, outside of an active event loop.
    # If this function were to be called from within an active event loop, this would be problematic.
    registry = asyncio.run(_collect())

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in registry:
            mcp.remove_tool(name)

    return registry


class _UTCFormatter(logging.Formatter):
    """Logging formatter that renders asctime in UTC (converter = time.gmtime).

    Ensures consistent, unambiguous timestamps for distributed systems and
    log analysis.
    """

    converter = time.gmtime


def main() -> None:
    """Initialize logging/auth/TLS state and run the MCP HTTPS server loop."""
    utc_fmt = _UTCFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(utc_fmt)
    logging.basicConfig(level=logging.INFO, handlers=[stream_handler], force=True)

    # Apply UTC formatter to uvicorn's own loggers so none slip through
    # with localtime or the default no-timestamp format.
    # Configure uvicorn's loggers to use the same stream handler and formatter
    # by ensuring they propagate messages and clearing their default handlers.
    for _uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _uv_logger = logging.getLogger(_uvicorn_logger_name)
        _uv_logger.handlers.clear()  # Clear default uvicorn handlers
        _uv_logger.addHandler(stream_handler)
        _uv_logger.propagate = (
            False  # Prevent propagation to root logger if it would cause duplication
        )

    # Suppress high-volume internal chatter from fastmcp / MCP protocol layers.
    # These fire on every request and are fully covered by mcp.request.* events.
    for _quiet_logger in (
        "mcp.server.lowlevel.server",
        "mcp.server.streamable_http_manager",
        "mcp.server.sse",
    ):
        logging.getLogger(_quiet_logger).setLevel(logging.WARNING)

    for cert_path in (_CERT_FILE, _KEY_FILE):
        if not os.path.exists(cert_path):
            logger.error("TLS file not found: %s", cert_path)
            sys.exit(1)

    auth_token = _require_env(_AUTH_TOKEN_ENV)
    oauth_config = load_oauth_config()
    oauth_service = _build_oauth_service(oauth_config)
    mcp = _build_server()

    # stateless_http=True: each POST is self-contained (no session ID tracking).
    # Anthropic's API client creates a new session per interaction rather than
    # reusing Mcp-Session-Id across the SSE stream and tool call POSTs, so
    # stateful mode silently drops all tool calls (routed to empty sessions).
    asgi_app = mcp.http_app(transport="streamable-http", stateless_http=True)

    if oauth_service is not None:
        for route in build_oauth_routes(oauth_service):
            asgi_app.router.routes.append(route)
        record(
            "mcp.oauth.server.started",
            issuer=oauth_service.issuer,
            token_endpoint=oauth_service.token_endpoint,
            authorization_endpoint=oauth_service.authorization_endpoint,
        )

    # Middleware composition order (outermost first):
    # AuthMiddleware → McpRequestEventsMiddleware → asgi_app
    # Rejected tokens terminate before mcp.request.started fires.
    evented_app = McpRequestEventsMiddleware(asgi_app)
    protected_app = AuthMiddleware(
        evented_app,
        token=auth_token,
        oauth_service=oauth_service,
    )

    logger.info("Starting MCP server on %s:%d", _HOST, _PORT)
    config = uvicorn.Config(
        protected_app,
        host=_HOST,
        port=_PORT,
        ssl_certfile=_CERT_FILE,
        ssl_keyfile=_KEY_FILE,
        log_level="info",
        access_log=False,
        timeout_keep_alive=1800,
    )
    config.load()

    orig_protocol_class = config.http_protocol_class

    class KeepaliveProtocol(orig_protocol_class):
        @override
        def connection_made(self, transport: BaseTransport) -> None:
            # get_extra_info('socket') returns a socket.socket object or None; narrow before keepalive setup.
            sock = transport.get_extra_info("socket")
            if isinstance(sock, socket.socket):
                _set_tcp_keepalive(sock)
            super().connection_made(transport)

    config.http_protocol_class = KeepaliveProtocol
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
