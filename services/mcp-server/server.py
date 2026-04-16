"""MCP server — Streamable HTTP transport with bearer token and OAuth 2.1 auth.

Internet-facing service at :443 (TLS). Exposes filesystem tools to
Anthropic models via the mcp_servers API parameter.

Security boundaries:
  - Auth admission via static bearer token or OAuth 2.1 (PKCE + S256)
  - TLS via Let's Encrypt certs mounted at /etc/letsencrypt (read-only)
  - Filesystem access sandboxed to /data/files (cortex sandbox) via volume mount
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
from mcp_events import record
from mcp_request_middleware import McpRequestEventsMiddleware
from mcp_toolprogress import toolprogress_begin, toolprogress_end
from oauth_config import OAuthServerConfig, load_oauth_config
from oauth_routes import build_oauth_routes
from oauth_service import OAuthService
from oauth_store import OAuthStore
from request_profile import current_profile
from response_size_guard import register_response_guard
from schema_compact import patch_fastmcp_tool_serialization
from starlette.middleware.gzip import GZipMiddleware
from tool_access import dispatch_denial_reason, is_dispatch_tool_allowed

from tools.advisor import register_advisor_tools
from tools.agent_bus import register_agent_bus_tools
from tools.agent_consult import register_agent_consult_tools
from tools.browser import register_browser_tools
from tools.context import register_context_tools
from tools.cortex import register_cortex_tools
from tools.cortex_named_tools import register_cortex_named_tools
from tools.document_ocr import register_document_ocr_tools
from tools.events import register_event_tools
from tools.filesystem import register_filesystem_tools
from tools.frontier import register_frontier_tools
from tools.frontier_imagine import register_imagine_tools
from tools.ingest_document import register_ingest_document_tools
from tools.llm import register_llm_tools
from tools.manage import register_manage_tools
from tools.markdown_tool import register_markdown_tools
from tools.model_status import register_model_status_tools
from tools.pipeline import register_pipeline_tools
from tools.pipeline_consult import register_pipeline_consult_tools
from tools.project import register_project_tools
from tools.quality import register_quality_tools
from tools.rag import register_rag_tools
from tools.rag_articles import register_rag_article_tools
from tools.security import register_security_tools
from tools.security_js import register_security_js_tools
from tools.sqlite import register_sqlite_tools
from tools.web import register_web_tools

if TYPE_CHECKING:
    from asyncio.transports import BaseTransport
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_AUTH_TOKEN_ENV = "MCP_AUTH_TOKEN"
_CERT_FILE = "/etc/letsencrypt/live/mcp.k-1.me/fullchain.pem"
_KEY_FILE = "/etc/letsencrypt/live/mcp.k-1.me/privkey.pem"
_HOST = "0.0.0.0"
_PORT = 443
_TCP_KEEPIDLE = 10
_TCP_KEEPINTVL = 10
_TCP_KEEPCNT = 3


def _env_truthy(name: str, default: bool) -> bool:
    """Return True if env var is set to a truthy value ('1', 'true', 'yes', 'on'), else default.

    Returns:
        True if the env var is truthy; otherwise the default.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


patch_fastmcp_tool_serialization()


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
    # LLM — provider-specific surfaces
    "grok_generate",
    "claude_generate",
    "openai_generate",
    # RAG (consolidated)
    "rag",
    # Response size guard
    "retrieve",
    # Domain dispatch (private layer — discovered from tools.local/)
    "email",
}


def _discover_private_tools(
    mcp: FastMCP,
) -> list[str]:
    """Discover and register tools from ``tools.local/`` (gitignored private layer).

    Walks ``tools.local/`` for modules containing ``register_*_tools(mcp)``
    functions — same convention as the static ``tools/`` registrations.
    Returns a list of registered tool names for logging.
    """
    import importlib
    import inspect
    import pkgutil

    registered: list[str] = []
    try:
        import tools.local as pkg  # noqa: PLC0415
    except ImportError:
        logger.info("No tools.local package found — private tools disabled")
        return registered

    for finder, mod_name, _is_pkg in pkgutil.iter_modules(
        pkg.__path__, prefix="tools.local."
    ):
        if mod_name.rsplit(".", 1)[-1].startswith("_"):
            continue
        try:
            module = importlib.import_module(mod_name)
        except Exception:
            logger.exception("Failed to import private tool module %s", mod_name)
            continue

        for attr_name, fn in inspect.getmembers(module, inspect.isfunction):
            if attr_name.startswith("register_") and attr_name.endswith("_tools"):
                try:
                    fn(mcp)
                    registered.append(f"{mod_name}.{attr_name}")
                except Exception:
                    logger.exception(
                        "Failed to register private tools from %s.%s",
                        mod_name,
                        attr_name,
                    )

    return registered


async def _tool_names(mcp: FastMCP) -> set[str]:
    """Return the set of currently registered tool names."""
    return {t.name for t in await mcp.list_tools()}


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
    register_ingest_document_tools(mcp)
    register_document_ocr_tools(mcp)
    register_pipeline_tools(mcp)
    register_pipeline_consult_tools(mcp)
    register_quality_tools(mcp)
    register_agent_bus_tools(mcp)
    register_agent_consult_tools(mcp)
    register_cortex_tools(mcp)
    register_cortex_named_tools(mcp)
    register_llm_tools(mcp)
    register_advisor_tools(mcp)
    register_frontier_tools(mcp)
    register_imagine_tools(mcp)
    register_security_tools(mcp)
    register_security_js_tools(mcp)

    _pre_private_tools = asyncio.run(_tool_names(mcp))
    _discover_private_tools(mcp)
    _post_private_tools = asyncio.run(_tool_names(mcp))
    _private_tool_names = _post_private_tools - _pre_private_tools

    @mcp.tool(title="Server Health Check")
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

    private_overflow: dict[str, Callable[..., Any]] = {}
    if _private_tool_names:
        private_overflow = {
            k: v for k, v in overflow_registry.items() if k in _private_tool_names
        }
        for k in private_overflow:
            del overflow_registry[k]

    valid_sandboxes = {"cortex", "workspaces"}
    sandbox_tool: dict[str, str] = {
        "cortex": "files",
    }
    md_op_map: dict[str, str] = {
        "md_list": "list_sections",
        "md_read": "read_section",
        "md_replace": "replace_section",
        "md_append": "append_section",
        "md_delete": "delete_section",
    }

    @mcp.tool(title="File I/O (Sandboxed)")
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
        binary: bool = False,
    ) -> dict[str, Any]:
        """File I/O across sandboxes (cortex, workspaces). Both sandbox and op are REQUIRED.

        `read` is unified across sandboxes: source files plus text-oriented
        document formats such as PDF, DOCX, ODT, EML, and HTML can be read in
        text mode from `cortex` or `workspaces`. Use `binary=True` only when another
        tool needs base64 file bytes instead of decoded text. Use `write_binary`
        (cortex sandbox only) to stage base64-encoded binary files (PDFs, images)
        — pass the base64 string as `content`. Use `move` to rename or relocate
        a file within the selected sandbox. Prefer the markdown ops for large
        structured docs when you need sections/TOC; for PDFs they operate on
        markdown produced internally by ``pymupdf4llm.to_markdown()``.

        **PDF extraction**: Default uses pymupdf4llm (prose-oriented markdown).
        For tabular or columnar PDFs (statements, invoices, ledger exports),
        prefer ``finance_extract_pdf(path=...)`` which uses pdfplumber and
        preserves table structure. PDF reads include an ``extraction`` field
        with method info and alternative suggestions.

        Sandboxes:
          cortex     — /data/files — user documents, notes, uploads, exports
          workspaces — /mnt/torus/projects/ — repository source, config, tasks, docs

        workspaces paths MUST include the repo name prefix:
          fs(sandbox="workspaces", op="read", path="universal-llm-gateway/tasks/specs/foo.md")
          fs(sandbox="workspaces", op="list", path="universal-llm-gateway/config")
          fs(sandbox="workspaces", op="list", path="universal-llm-gateway")  ← repo root

        Use op="list" for directories; op="read" on a directory path returns an error.

        Standard ops:
          read           (path)                           — read file (text or PDF/DOCX/ODT/EML/HTML)
          read_multi     (paths: list)                    — read multiple files
          write          (path, content)                  — write/create file
          append         (path, content)                  — append to file
          prepend        (path, content)                  — prepend to file
          replace        (path, target, content, all_occurrences?) — replace text
          insert_at_line (path, content, line)            — insert at line number
          list           (path?)                          — list directory
          delete         (path)                           — delete file
          search         (path, content)                  — regex search (workspaces sandbox only)
          move           (path, target)                   — rename/relocate file
          copy           (path, target)                   — copy file (both sandboxes)
          write_binary   (path, content)                  — write base64-encoded binary (cortex sandbox only)

        Markdown section ops (for large docs):
          md_list    (path)                    — list sections/TOC (also works on PDF/DOCX/ODT/EML via auto-converted markdown)
          md_read    (path, section)           — read one section (also works on PDF/DOCX/ODT/EML via auto-converted markdown)
          md_replace (path, section, content)  — replace section (text files only)
          md_append  (path, section, content)  — append to section (text files only)
          md_delete  (path, section)           — delete section (text files only)
        Converted formats such as PDF are read-only for markdown section ops:
        use ``md_list`` / ``md_read`` to inspect them, not ``md_replace`` /
        ``md_append`` / ``md_delete``.
        """
        if not op:
            return {"error": "'op' is required"}
        if sandbox not in valid_sandboxes:
            return {
                "error": f"sandbox must be 'cortex' or 'workspaces', got {sandbox!r}"
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

        if sandbox == "workspaces":
            if op == "read":
                fn = overflow_registry.get("read_project_file")
                if fn is None:
                    return {"error": "read_project_file tool not available"}
                return fn(path, binary=binary)
            if op == "write":
                fn = overflow_registry.get("write_project_file")
                if fn is None:
                    return {"error": "write_project_file tool not available"}
                return fn(path, content)
            if op == "list":
                fn = overflow_registry.get("list_project_files")
                if fn is None:
                    return {"error": "list_project_files tool not available"}
                return fn(path, include_untracked=include_untracked)
            if op == "search":
                fn = overflow_registry.get("search_project_files")
                if fn is None:
                    return {"error": "search_project_files tool not available"}
                return fn(content, directory=path, include_untracked=include_untracked)
            if op in {"append", "prepend"}:
                fn = overflow_registry.get("edit_project_file")
                if fn is None:
                    return {"error": "edit_project_file tool not available"}
                return fn(path, op, content)
            if op == "replace":
                fn = overflow_registry.get("edit_project_file")
                if fn is None:
                    return {"error": "edit_project_file tool not available"}
                return fn(
                    path,
                    "replace",
                    content,
                    target_str=target,
                    all_occurrences=all_occurrences,
                )
            if op == "insert_at_line":
                fn = overflow_registry.get("edit_project_file")
                if fn is None:
                    return {"error": "edit_project_file tool not available"}
                return fn(path, "insert_at_line", content, line=line)
            if op == "move":
                fn = overflow_registry.get("move_project_file")
                if fn is None:
                    return {"error": "move_project_file tool not available"}
                return fn(path, target)
            if op == "copy":
                fn = overflow_registry.get("copy_project_file")
                if fn is None:
                    return {"error": "copy_project_file tool not available"}
                return fn(path, target)
            valid = "read, write, append, prepend, replace, insert_at_line, move, copy, list, search"
            return {"error": f"Unknown workspaces op: {op!r}. Available: {valid}"}

        tool_name = sandbox_tool[sandbox]
        fn = overflow_registry.get(tool_name)
        if fn is None:
            return {"error": f"{tool_name} tool not available"}
        if sandbox == "cortex":
            if op == "read_multi":
                paths = paths or []
                return fn(
                    op=op,
                    path=path,
                    paths=paths,
                    content=content,
                    target=target,
                    binary=binary,
                )
            return fn(
                op=op,
                path=path,
                content=content,
                target=target,
                line=line,
                all_occurrences=all_occurrences,
                binary=binary,
            )
        return fn(
            op=op,
            path=path,
            content=content,
            target=target,
            line=line,
            all_occurrences=all_occurrences,
            binary=binary,
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

    @mcp.tool(title="RAG Knowledge Retrieval")
    async def rag(op: str, arguments: str = "{}") -> Any:
        """RAG knowledge retrieval and index management — dispatch by op name.

        op: operation name (see table below)
        arguments: JSON string with operation arguments

        Operations:
          search            (query, scope?, prefix?, top_k?)    — semantic search; scope/prefix are mutually exclusive
          answer            (question, scope?, prefix?, deep?)  — RAG-grounded answer; deep=true for iterative retrieval
          list_scopes       ()                                  — list scopes with prefixes and coverage
          coverage          ()                                  — per-scope indexed file counts
          upsert_article    (url, title?, scope?)               — index article by URL
          delete_source     (source_hash)                       — delete indexed source by hash
          refresh_hints     (scope?)                            — regenerate discriminative vocabulary hints
          orphaned_articles ()                                  — find articles not in any scope
          delete_directory  (directory)                         — delete all indexed content under a path

        Example:
          rag(op="search", arguments='{"query": "embedding strategies", "scope": "research"}')
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

    @mcp.tool(title="Tool Dispatcher")
    async def dispatch(tool: str, arguments: str = "{}") -> Any:
        """Call any server tool by name — gateway to tools beyond the primary set.

        Full catalog: fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="dispatch")
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
            if private_overflow and tool in private_overflow:
                raise ValueError(
                    f"Tool {tool!r} is a personal tool — use "
                    f"private_dispatch(tool={tool!r}, ...) instead."
                )
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

    if private_overflow:
        _PRIMARY_TOOLS.add("private_dispatch")

        @mcp.tool(title="Private Tool Dispatcher")
        async def private_dispatch(tool: str, arguments: str = "{}") -> Any:
            """Call personal/domain-specific tools by name.

            Use `private_dispatch(tool="list")` to see available tools.
            """
            import json as _json

            if tool == "list":
                return {"available_tools": sorted(private_overflow)}

            fn = private_overflow.get(tool)
            if fn is None:
                raise ValueError(
                    f"Unknown private tool: {tool!r}. "
                    f"Available: {sorted(private_overflow)}"
                )
            parsed = _json.loads(arguments)
            record("mcp.tool.private.called", tool=tool)
            result = fn(**parsed)
            if asyncio.iscoroutine(result):
                result = await result
            record("mcp.tool.private.success", tool=tool)
            if hasattr(result, "model_dump"):
                return result
            return {"tool": tool, "result": result}

    primary_count = len(_PRIMARY_TOOLS)
    overflow_count = len(overflow_registry)
    private_count = len(private_overflow)
    logger.info(
        "Tool pruning: %d primary (advertised), %d overflow (via dispatch), "
        "%d private (via private_dispatch)",
        primary_count,
        overflow_count,
        private_count,
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
    # AuthMiddleware → McpRequestEventsMiddleware → GZip → asgi_app
    # Rejected tokens terminate before mcp.request.started fires.
    compressed_app = GZipMiddleware(asgi_app, minimum_size=500)
    evented_app = McpRequestEventsMiddleware(compressed_app)
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
