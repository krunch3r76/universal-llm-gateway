"""MCP server — Streamable HTTP transport with bearer token and OAuth 2.1 auth.

Internet-facing service at :443 (TLS). Exposes filesystem tools to
Anthropic models via the mcp_servers API parameter.

Security boundaries:
  - Auth admission via static bearer token or OAuth 2.1 (PKCE + S256)
  - TLS via Let's Encrypt certs mounted at /etc/letsencrypt (read-only)
  - Filesystem access sandboxed to /data/files via volume mount
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from asyncio.transports import BaseTransport
from typing import Any, cast, override

import uvicorn
from auth_middleware import AuthMiddleware
from fastmcp import FastMCP
from mcp_events import monotonic_now, record
from mcp_request_middleware import McpRequestEventsMiddleware
from oauth_config import OAuthServerConfig, load_oauth_config
from oauth_routes import build_oauth_routes
from oauth_service import OAuthService
from oauth_store import OAuthStore
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.types import Send

from tools.browser import register_browser_tools
from tools.clip import register_clip_tools
from tools.context import register_context_tools
from tools.events import register_event_tools
from tools.filesystem import register_filesystem_tools
from tools.manage import register_manage_tools
from tools.pipeline import register_pipeline_tools
from tools.pipeline_consult import register_pipeline_consult_tools
from tools.project import register_project_tools
from tools.quality import register_quality_tools
from tools.rag import register_rag_tools
from tools.rag_articles import register_rag_article_tools
from tools.sqlite import register_sqlite_tools
from tools.web import register_web_tools

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
    """Force sse_starlette to emit real SSE events every N seconds.

    Middleboxes and firewalls may categorise SSE comment-only pings as
    empty traffic.  Sending a named event with a payload resets idle
    timers.  We inject ping_message_factory and ping interval via kwargs
    so they're set during the original __init__ constructor path.
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
    # type: ignore[method-assign] required because the patched signature diverges from the original.
    EventSourceResponse.__init__ = _patched_init  # type: ignore[method-assign]


def _patch_sse_lifecycle_events() -> None:
    """Emit structured events for every SSE stream start/end."""
    # Accessing protected member for lifecycle-event monkey-patch.
    _orig_stream = EventSourceResponse._stream_response  # type: ignore[attr-defined]

    async def _stream_with_events(self: EventSourceResponse, send: Send) -> None:
        t0 = monotonic_now()
        record("mcp.sse.stream.started")
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
            logger.warning("SSE stream aborted after %.1fs: %s", duration, exc)
            raise
        else:
            duration = monotonic_now() - t0
            record(
                "mcp.sse.stream.ended",
                duration_s=round(duration, 3),
                reason="clean",
            )
            # Only log streams that did real work; sub-100ms = ListTools handshake.
            if duration >= 0.1:
                logger.info("SSE stream ended cleanly after %.1fs", duration)

    # Monkey-patch protected method to wrap stream with start/end events.
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


def _build_oauth_service(config: OAuthServerConfig | None) -> OAuthService | None:
    """Construct an OAuthService from config, or None if OAuth is disabled."""
    if config is None:
        return None
    store = OAuthStore()
    return OAuthService(config=config, store=store)


_PRIMARY_TOOLS: set[str] = {
    "write_file",
    "read_file",
    "edit_file",
    "delete_file",
    "list_files",
    "write_context_file",
    "read_context_file",
    "edit_context_file",
    "delete_context_file",
    "list_context_directory",
    "read_project_file",
    "list_project_files",
    "sqlite_query",
    "sqlite_execute",
    "sqlite_schema",
    "sqlite_list_databases",
    "web_search",
    "pipeline_run",
    "manage_service",
    "quality_gate",
    "list_journal_entries",
    "list_clips",
    "health",
    "dispatch",
}


def _build_server() -> FastMCP:
    """Construct and configure the FastMCP application with all registered tools."""
    mcp: FastMCP = FastMCP("gateway-tools")
    register_filesystem_tools(mcp)
    register_manage_tools(mcp)
    register_project_tools(mcp)
    register_web_tools(mcp)
    register_rag_tools(mcp)
    register_rag_article_tools(mcp)
    if _env_truthy("ENABLE_CONTEXT_TOOLS", default=True):
        register_context_tools(mcp)
    else:
        logger.info("Context tools disabled (ENABLE_CONTEXT_TOOLS=false)")
    register_clip_tools(mcp)
    if _env_truthy("ENABLE_BROWSER_TOOLS", default=False):
        register_browser_tools(mcp)
    else:
        logger.info("Browser tools disabled (ENABLE_BROWSER_TOOLS=false)")
    register_sqlite_tools(mcp)
    register_event_tools(mcp)
    register_pipeline_tools(mcp)
    register_pipeline_consult_tools(mcp)
    register_quality_tools(mcp)

    @mcp.tool()
    def health() -> dict[str, str]:
        """Health check — confirms the MCP server is reachable."""
        return {"status": "ok"}

    overflow_registry: dict[str, Any] = _prune_to_primary(mcp)

    @mcp.tool()
    def dispatch(tool: str, arguments: str = "{}") -> dict[str, str]:
        """Call any server tool by name — gateway to tools beyond the primary set.

        Some MCP clients enumerate only a limited number of tools. Use dispatch
        to reach any tool not in your direct list.

        Dispatchable tools:
          File ops:
            view_image(path, max_dimension?, quality?) — view photo/screenshot
            move_file(source, destination) — move/rename any file
            copy_file(source, destination) — copy any file
            remove_directory(directory) — delete directory and contents
          Search & knowledge:
            rag_search(query, scope?, limit?) — semantic search
            rag_answer(question, scope?) — RAG-grounded answer
            rag_list_scopes() — list available scopes
            rag_upsert_article(url, title?, scope?) — index article
            search_project_files(query, glob?) — search source code
          Web:
            web_fetch(url) — fetch URL content
          Observability:
            query_observability(operation, params?) — event queries
          Pipeline:
            pipeline_consult(execution_id, step_name, problem)
            validate_pipeline(path)
          Journal & clips:
            read_journal_entry(id) — read entry
            write_journal_entry(title, content, tags?)
            read_clip(name) — read a clip
            list_todos() — list todo items
          Browser (if enabled):
            browser_navigate, browser_click, browser_fill,
            browser_screenshot, browser_get_structure, browser_get_content

        Example:
            dispatch(tool="view_image", arguments='{"path": "photos/note.jpg"}')
            dispatch(tool="move_file", arguments='{"source": "a.jpg", "destination": "b/a.jpg"}')

        Args:
            tool: Name of the tool to invoke.
            arguments: JSON string of tool arguments (default "{}").

        Returns:
            {"tool": "<name>", "result": "<JSON string of tool output>"}
        """
        import json as _json

        fn = overflow_registry.get(tool)
        if fn is None:
            raise ValueError(
                f"Unknown dispatch tool: {tool!r}. "
                f"Available: {sorted(overflow_registry)}"
            )
        parsed = _json.loads(arguments)
        result = fn(**parsed)
        return {"tool": tool, "result": _json.dumps(result)}

    primary_count = sum(1 for _ in _PRIMARY_TOOLS)
    logger.info(
        "Tool pruning: %d primary (advertised), %d overflow (via dispatch)",
        primary_count,
        len(overflow_registry),
    )
    return mcp


def _prune_to_primary(mcp: FastMCP) -> dict[str, Any]:
    """Remove non-primary tools from MCP and return their fn references."""
    import asyncio

    async def _collect() -> dict[str, Any]:
        registry: dict[str, Any] = {}
        all_tools = await mcp.list_tools()
        for t in all_tools:
            if t.name not in _PRIMARY_TOOLS:
                tool_obj = await mcp.get_tool(t.name)
                registry[t.name] = tool_obj.fn
        return registry

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
    utc_fmt = _UTCFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(utc_fmt)
    logging.basicConfig(level=logging.INFO, handlers=[stream_handler], force=True)

    # Apply UTC formatter to uvicorn's own loggers so none slip through
    # with localtime or the default no-timestamp format.
    for _uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _uv_logger = logging.getLogger(_uvicorn_logger_name)
        _uv_logger.handlers.clear()
        _uv_logger.addHandler(stream_handler)
        _uv_logger.propagate = False

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
            # get_extra_info('socket') returns the raw socket; cast for _set_tcp_keepalive.
            sock = cast(socket.socket | None, transport.get_extra_info("socket"))
            if sock is not None:
                _set_tcp_keepalive(sock)
            super().connection_made(transport)

    config.http_protocol_class = KeepaliveProtocol
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
