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
import logging  # stdlib needed for _UTCFormatter / uvicorn integration below
import os
import signal
import socket
import sys
import time
from typing import TYPE_CHECKING, Any, override

import uvicorn
from auth_middleware import AuthMiddleware
from dual_endpoint_http import build_dual_endpoint_app
from edge_telemetry_middleware import EdgeTelemetryMiddleware
from endpoint_surface import (
    Surface,
    derive_surface_primary_tools,
    filter_overflow_metadata_for_surface,
)
from fastmcp import FastMCP
from mcp_events import flush, record
from mcp_request_middleware import McpRequestEventsMiddleware
from mcp_toolprogress import toolprogress_begin, toolprogress_end
from middleware.drain import (
    DrainMiddleware,
    begin_drain,
    complete_drain,
    in_flight_count,
)
from oauth_config import OAuthServerConfig, load_oauth_config
from oauth_routes import build_oauth_routes
from oauth_service import OAuthService
from oauth_store import OAuthStore
from request_profile import current_profile
from response_size_guard import register_response_guard
from schema_compact import register_compact_schema_transform
from starlette.middleware.gzip import GZipMiddleware
from surface_enum import register_surface_enum_transform
from surface_registration import register_tools_for_surface
from tool_access import dispatch_denial_reason, is_dispatch_tool_allowed
from fs_description import build_fs_tool_description
from fs_impl import fs_impl
from tool_error_enricher import register_tool_error_enricher
from tool_search import capture_overflow_metadata, register_tool_search_tool
from tools._agent_tools import JsonArgStr
from tools.filesystem._cross_sandbox import copy_between_sandboxes_impl
from universal_logging import get_logger

if TYPE_CHECKING:
    from asyncio.transports import BaseTransport
    from collections.abc import Callable
    from types import FrameType

logger = get_logger(__name__)

_AUTH_TOKEN_ENV = "MCP_AUTH_TOKEN"
_CERT_FILE = "/etc/letsencrypt/live/mcp.k-1.me/fullchain.pem"
_KEY_FILE = "/etc/letsencrypt/live/mcp.k-1.me/privkey.pem"
_HOST = "0.0.0.0"
_PORT = 443
_TCP_KEEPIDLE = 10
_TCP_KEEPINTVL = 10
_TCP_KEEPCNT = 3
_GRACEFUL_SHUTDOWN_TIMEOUT_S = 25


def _tool_error_envelope(
    tool: str, op: str | None, exc: BaseException
) -> dict[str, Any]:
    # ⟹ never let an exception cross the MCP boundary opaquely; Anthropic's
    # client wraps unhandled exceptions as the generic "Error occurred during
    # tool execution" envelope, stripping all diagnostic content.
    message = str(exc) or type(exc).__name__
    record(
        "mcp.tool.error",
        tool=tool,
        op=op or "",
        error=message,
        error_type=type(exc).__name__,
    )
    return {
        "error": message,
        "error_type": type(exc).__name__,
        "tool": tool,
        "op": op,
    }


def _env_truthy(name: str, default: bool) -> bool:
    """Return True if env var is set to a truthy value ('1', 'true', 'yes', 'on'), else default.

    Returns:
        True if the env var is truthy; otherwise the default.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


from _derive import (  # noqa: E402, I001
    derive_claude_manifest as _derive_claude_manifest,
    get_claude_manifest,  # noqa: F401 — re-exported for test access
)

_claude_manifest = _derive_claude_manifest()
_PRIMARY_TOOLS: set[str] = set(derive_surface_primary_tools("code"))


def _discover_private_tools(
    mcp: FastMCP,
    *,
    surface: Surface = "code",
) -> list[str]:
    """Discover and register tools from ``tools.local/`` (gitignored private layer).

    Walks ``tools.local/`` for modules containing ``register_*_tools(mcp)``
    functions — same convention as the static ``tools/`` registrations.
    On ``surface="life"`` only modules in ``LIFE_PRIVATE_TOOL_MODULES`` load;
    code retains the full private tree.
    Returns a list of registered tool names for logging.
    """
    import importlib
    import inspect
    import pkgutil

    registered: list[str] = []
    try:
        import tools.local as pkg  # noqa: PLC0415
        from tools.local._life_private_allowlist import (  # noqa: PLC0415
            LIFE_PRIVATE_TOOL_MODULES,
        )
    except ImportError:
        logger.info("No tools.local package found — private tools disabled")
        return registered

    for finder, mod_name, _is_pkg in pkgutil.iter_modules(
        pkg.__path__, prefix="tools.local."
    ):
        short_name = mod_name.rsplit(".", 1)[-1]
        if short_name.startswith("_"):
            continue
        if surface == "life" and short_name not in LIFE_PRIVATE_TOOL_MODULES:
            continue
        try:
            module = importlib.import_module(mod_name)
        except Exception:
            logger.exception("Failed to import private tool module %s", mod_name)
            record("mcp.tool.private.import.failed", module=mod_name)
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
                    record(
                        "mcp.tool.private.register.failed",
                        module=mod_name,
                        attr=attr_name,
                    )

    return registered


async def _tool_names(mcp: FastMCP) -> set[str]:
    """Return the set of currently registered tool names."""
    return {t.name for t in await mcp.list_tools()}


def _build_server(
    surface: Surface = "code",
) -> tuple[
    FastMCP,
    dict[str, tuple[str, dict[str, Any]]],
    dict[str, Any],
]:
    """Construct a surface-scoped FastMCP server and prune exports.

    The resulting server advertises a primary tool set and routes non-primary
    tools through `dispatch` for clients with limited tool enumeration capacity.

    Returns:
        Tuple of (mcp, overflow_metadata, overflow_registry).
        overflow_registry: callables for rag and other demoted inline wrappers.
    """
    primary_tools = set(derive_surface_primary_tools(surface))
    mcp: FastMCP = FastMCP(f"gateway-tools-{surface}")
    register_tools_for_surface(mcp, surface)
    _discover_private_tools(mcp, surface=surface)

    @mcp.tool(title="Server Health Check")
    def health() -> dict[str, str]:
        """Health check — confirms the MCP server is reachable."""
        return {"status": "ok"}

    try:
        register_tool_error_enricher(mcp)
    except Exception:
        logger.exception(
            "Failed to initialize tool error enricher — proceeding without it"
        )
        record("mcp.tool.enricher.init.failed", error="see server logs")
    try:
        register_response_guard(mcp)
    except Exception:
        logger.exception(
            "Failed to initialize response size guard — proceeding without it"
        )
        record("mcp.response.guard.init.failed", error="see server logs")

    register_compact_schema_transform(mcp)
    register_surface_enum_transform(mcp, surface)

    # Capture descriptions BEFORE pruning — _prune_to_primary removes the
    # underlying Tool objects, so post-prune metadata reads return empty.
    overflow_metadata = asyncio.run(
        capture_overflow_metadata(mcp, frozenset(primary_tools))
    )
    overflow_metadata = filter_overflow_metadata_for_surface(overflow_metadata, surface)
    from _coherence_allowlist import INTENTIONAL_OVERFLOW  # noqa: PLC0415
    from _derive import run_startup_tool_coherence_checks  # noqa: PLC0415

    run_startup_tool_coherence_checks(
        primary_tools,
        primary_tools | set(overflow_metadata.keys()),
        allowlist=INTENTIONAL_OVERFLOW,
    )
    overflow_registry: dict[str, Callable[..., Any]] = _prune_to_primary(
        mcp, primary_tools
    )
    register_tool_search_tool(mcp, overflow_metadata, surface=surface)

    _fs_tool_description = build_fs_tool_description(surface)

    @mcp.tool(title="File I/O (Sandboxed)", description=_fs_tool_description)
    def fs(
        op: str,
        sandbox: str = "",
        description: str = "",
        path: str = "",
        paths: list[str] | None = None,
        content: str = "",
        target: str = "",
        target_sandbox: str = "",
        line: int = 0,
        section: str = "",
        all_occurrences: bool = False,
        include_untracked: bool = True,
        binary: bool = False,
        max_depth: int = 3,
        offset: int = 0,
        limit: int = 0,
        expected_sha256: str = "",
        if_absent: bool = False,
        heading: str = "",
        level: int = 0,
        position: str = "",
        mode: str = "",
        thread: str | None = None,
    ) -> dict[str, Any]:
        """Sandboxed file I/O (cortex, workspaces). Full catalog in tool description."""
        try:
            return fs_impl(
                surface=surface,
                overflow_registry=overflow_registry,
                op=op,
                sandbox=sandbox,
                path=path,
                paths=paths,
                content=content,
                target=target,
                target_sandbox=target_sandbox,
                line=line,
                section=section,
                all_occurrences=all_occurrences,
                include_untracked=include_untracked,
                binary=binary,
                max_depth=max_depth,
                offset=offset,
                limit=limit,
                expected_sha256=expected_sha256,
                if_absent=if_absent,
                heading=heading,
                level=level,
                position=position,
                mode=mode,
                thread=thread,
            )
        except Exception as exc:
            return _tool_error_envelope("fs", op, exc)

    rag_op_tool: dict[str, str] = {
        "search": "rag_search",
        "recon": "rag_recon",
        "list_mapped": "rag_list_mapped",
        "list_scopes": "rag_list_scopes",
        "coverage": "rag_coverage",
        "upsert_article": "rag_upsert_article",
        "delete_source": "rag_delete_source",
        "refresh_hints": "rag_refresh_corpus_hints",
        "orphaned_articles": "rag_orphaned_articles",
        "delete_directory": "rag_delete_directory",
    }

    @mcp.tool(title="RAG Knowledge Retrieval")
    async def rag(op: str, arguments: JsonArgStr = "{}") -> Any:
        """RAG knowledge retrieval and index management — dispatch by op name.

        op: operation name (see table below)
        arguments: JSON-encoded object string (e.g. '{"query": "...", "scope": "research"}')

        Operations:
          search            (query, scope?, prefix?, top_k?|limit?, mapped?) — semantic search (PRIMARY and ONLY agent surface for RAG); scope/prefix mutually exclusive; limit aliases top_k; mapped=true does exact (scope,query) durable-pack lookup via config/mcp/rag_mapped_index.yaml (identical envelope) with live rag-context fallback on miss. Agents MUST use this (or dedicated rag_search); rag_answer pipeline is buried for MCP debugging of /v1/chat/completions with rag-answer* models only.
          recon             (label, themes, top_k?, durable_sink?) — labeled per-theme recon with durable sidecar persistence via DurableSink
          list_mapped       ()                                  — URI-safe catalog of mapped pack keys + activate recipe
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
        from tools._agent_tools import (
            dispatch_arguments_error,
            parse_dispatch_arguments,
        )

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
            args = parse_dispatch_arguments(arguments)
            if args is None:
                return dispatch_arguments_error(arguments, example='{"query": "..."}')

            result = fn(**args)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:
            err = str(exc) or type(exc).__name__
            return _tool_error_envelope("rag", op, exc)
        finally:
            toolprogress_end(t_prog, prog_timer, "rag", error=err, op=op)

    @mcp.tool(title="Tool Dispatcher")
    async def dispatch(tool: str, arguments: JsonArgStr = "{}") -> Any:
        """Invoke a non-primary tool by name. Discover candidates via tool_search first.

        arguments: JSON-encoded object string (e.g. '{"key": "value"}').
        Use tool_search(query="...") to locate the tool name and dispatch_template.
        """
        from tools._agent_tools import (
            dispatch_arguments_error,
            parse_dispatch_arguments,
        )

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
            from tool_search_matcher import dispatch_rejection_for_primary_tool

            primary_rejection = dispatch_rejection_for_primary_tool(
                tool, primary_tools=primary_tools
            )
            if primary_rejection is not None:
                record(
                    "mcp.tool.dispatch.primary_rejected",
                    tool=tool,
                )
                return {"tool": tool, "result": {"error": primary_rejection}}
            record("mcp.tool.dispatch.unknown", tool=tool)
            return {
                "tool": tool,
                "result": {
                    "error": (
                        f"Unknown dispatch tool: {tool!r}. "
                        f"Available: {sorted(overflow_registry)}"
                    )
                },
            }
        parsed = parse_dispatch_arguments(arguments)
        if parsed is None:
            return {
                "tool": tool,
                "result": dispatch_arguments_error(
                    arguments, example='{"key": "value"}'
                ),
            }
        record(
            "mcp.profile.dispatch.routed",
            profile=profile,
            tool=tool,
        )
        _desc, _schema = overflow_metadata.get(tool, ("", {}))
        from tools._overflow_dispatch import (  # noqa: PLC0415
            enrich_type_error_for_nested_op,
            preflight_nested_op_dispatch,
        )

        preflight = preflight_nested_op_dispatch(tool, parsed, _schema)
        if preflight is not None:
            record(
                "mcp.tool.dispatch.shape_error",
                tool=tool,
                op=str(parsed.get("op") or ""),
            )
            return {"tool": tool, "result": preflight}
        try:
            result = fn(**parsed)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            envelope = _tool_error_envelope(
                tool, str(parsed.get("op") or "") or None, exc
            )
            hint_extra = enrich_type_error_for_nested_op(tool, parsed, exc, _schema)
            if hint_extra is not None:
                envelope.update(hint_extra)
            return {"tool": tool, "result": envelope}
        record("mcp.tool.dispatch.success", tool=tool)
        if hasattr(result, "model_dump"):
            return result
        return {"tool": tool, "result": result}

    # Inline wrappers (rag) are defined after _prune_to_primary because they
    # close over overflow_registry. They leak into the advertised catalog
    # unless explicitly demoted to overflow here. ∀ inline wrapper not in
    # _PRIMARY_TOOLS: capture metadata + callable, then remove from mcp.
    _demote_inline_wrappers(mcp, overflow_registry, overflow_metadata, primary_tools)
    import tool_search as _ts_module  # noqa: PLC0415
    from tool_search import build_manifest_from_metadata as _build_mf  # noqa: PLC0415

    _ts_module._MANIFEST = _build_mf(overflow_metadata)

    primary_count = len(primary_tools)
    overflow_count = len(overflow_registry)
    logger.info(
        "Tool pruning [%s]: %d primary (advertised), %d overflow (via dispatch)",
        surface,
        primary_count,
        overflow_count,
    )
    if overflow_registry:
        logger.info(
            "Overflow tools (not in _PRIMARY_TOOLS — add to promote): %s",
            sorted(overflow_registry),
        )
    return mcp, overflow_metadata, overflow_registry


def _demote_inline_wrappers(
    mcp: FastMCP,
    overflow_registry: dict[str, Callable[..., Any]],
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]],
    primary_tools: set[str] | frozenset[str],
) -> None:
    """Move any post-prune inline wrappers (e.g. rag) out of the advertised catalog.

    These wrappers close over ``overflow_registry`` so they are necessarily
    defined after ``_prune_to_primary`` runs. Any such wrapper whose name is
    not in ``_PRIMARY_TOOLS`` must be moved into the overflow registry and
    its description captured so ``tool_search`` can surface it.
    """
    import warnings

    async def _capture_and_remove() -> None:
        for tool in await mcp.list_tools():
            if tool.name in primary_tools or tool.name in overflow_registry:
                continue
            tool_obj = await mcp.get_tool(tool.name)
            overflow_registry[tool.name] = tool_obj.fn
            description = getattr(tool_obj, "description", "") or ""
            schema = (
                getattr(tool_obj, "parameters", None)
                or getattr(tool_obj, "inputSchema", None)
                or {}
            )
            overflow_metadata[tool.name] = (description, schema)

    asyncio.run(_capture_and_remove())
    current_tool_names = asyncio.run(_tool_names(mcp))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in list(overflow_metadata):
            if name not in current_tool_names:
                continue
            if name in primary_tools:
                continue
            mcp.remove_tool(name)


def _prune_to_primary(
    mcp: FastMCP,
    primary_tools: set[str] | frozenset[str] | None = None,
) -> dict[str, Callable[..., Any]]:
    """Remove non-primary tools from the exported MCP catalog.

    Returns a registry of removed callables so `dispatch` can still invoke
    them by name while keeping the advertised tool list intentionally compact.
    """
    import asyncio

    keep = set(primary_tools if primary_tools is not None else _PRIMARY_TOOLS)

    async def _collect() -> dict[str, Callable[..., Any]]:
        registry: dict[str, Callable[..., Any]] = {}
        all_tools = await mcp.list_tools()
        for t in all_tools:
            if t.name not in keep:
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


class _AcceptNormalizeMiddleware:
    """Ensure ``Accept`` includes both ``application/json`` and ``text/event-stream``.

    Remote MCP clients (OpenAI Responses API, xAI) may not send the exact
    Accept header that the MCP SDK's Streamable HTTP transport requires,
    resulting in 406.  This middleware normalises the header before the
    transport layer checks it.
    """

    _REQUIRED = "application/json, text/event-stream"

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode()
            needs_fix = (
                "application/json" not in accept or "text/event-stream" not in accept
            )
            if needs_fix:
                new_headers = [(k, v) for k, v in scope["headers"] if k != b"accept"]
                new_headers.append((b"accept", self._REQUIRED.encode()))
                scope = {**scope, "headers": new_headers}
        await self._app(scope, receive, send)


class _UTCFormatter(logging.Formatter):
    """Logging formatter that renders asctime in UTC (converter = time.gmtime).

    Ensures consistent, unambiguous timestamps for distributed systems and
    log analysis.
    """

    converter = time.gmtime


def _emit_claude_boot_shadow_log() -> None:
    """Emit structured boot line for /mcp Claude manifest health.

    ∀ boot: logs domain_count + tool_names_sha256 for drift detection.
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    from _derive import get_claude_manifest  # noqa: PLC0415, F811

    manifest = get_claude_manifest()
    tool_names = sorted(e["tool_name"] for e in manifest)
    names_sha256 = hashlib.sha256(json.dumps(tool_names).encode()).hexdigest()
    logger.info(
        "claude_manifest_boot domain_count=%d names_sha256=%s",
        len(manifest),
        names_sha256,
    )


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
    life_mcp, _, _ = _build_server("life")
    code_mcp, _, _ = _build_server("code")

    # Dual mounts /mcp/life + /mcp/code (bare /mcp absent). stateless_http=True:
    # each POST is self-contained — Anthropic's client creates a new session per
    # interaction rather than reusing Mcp-Session-Id, so stateful mode drops calls.
    asgi_app = build_dual_endpoint_app(life_mcp, code_mcp)
    from dual_endpoint_http import SurfaceStampMiddleware  # noqa: PLC0415

    stamped_app = SurfaceStampMiddleware(asgi_app)

    if oauth_service is not None:
        for route in build_oauth_routes(oauth_service):
            asgi_app.router.routes.append(route)
        record(
            "mcp.oauth.server.started",
            issuer=oauth_service.issuer,
            token_endpoint=oauth_service.token_endpoint,
            authorization_endpoint=oauth_service.authorization_endpoint,
        )

    _emit_claude_boot_shadow_log()

    # Middleware composition order (outermost first):
    # Drain → EdgeTelemetry → AuthMiddleware → McpRequestEventsMiddleware → AcceptNormalize → GZip → asgi_app
    # Drain rejects new tool calls during shutdown before they enter the
    # authenticated tool path, while already-admitted requests keep running.
    # EdgeTelemetry observes every HTTP request before any auth or routing
    # decisions, so traffic that AuthMiddleware short-circuits (/health,
    # CORS preflights) and traffic from external probes still produces an
    # mcp.edge.request.observed record.  Rejected tokens still terminate
    # before mcp.request.started fires.
    compressed_app = GZipMiddleware(stamped_app, minimum_size=500)
    accept_app = _AcceptNormalizeMiddleware(compressed_app)
    evented_app = McpRequestEventsMiddleware(accept_app)
    protected_app = AuthMiddleware(
        evented_app,
        token=auth_token,
        oauth_service=oauth_service,
    )
    observed_app = EdgeTelemetryMiddleware(protected_app)
    drain_app = DrainMiddleware(observed_app)

    logger.info("Starting MCP server on %s:%d", _HOST, _PORT)
    config = uvicorn.Config(
        drain_app,
        host=_HOST,
        port=_PORT,
        ssl_certfile=_CERT_FILE,
        ssl_keyfile=_KEY_FILE,
        log_level="info",
        access_log=False,
        timeout_keep_alive=1800,
        timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_TIMEOUT_S,
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

    class DrainAwareServer(uvicorn.Server):
        @override
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            try:
                signal_name = signal.Signals(sig).name
            except ValueError:
                signal_name = str(sig)
            begin_drain(
                reason=f"signal:{signal_name}",
                timeout_s=_GRACEFUL_SHUTDOWN_TIMEOUT_S,
            )
            super().handle_exit(sig, frame)

    server = DrainAwareServer(config)
    try:
        server.run()
    finally:
        # If uvicorn's graceful_shutdown timed out, requests that were in-flight at
        # SIGTERM are still counted because their finally blocks never ran — uvicorn
        # cancelled them. A non-zero count at exit is the proxy for "drain timed out".
        timed_out = in_flight_count() > 0
        complete_drain(timed_out=timed_out)
        # server.run() has returned, so the event loop is torn down and the
        # daemon UDS publisher thread would die with drain.completed still
        # queued. Block briefly until the publisher drains so the completion
        # event (and any tail events) actually reach the event service.
        flush()


if __name__ == "__main__":
    main()
