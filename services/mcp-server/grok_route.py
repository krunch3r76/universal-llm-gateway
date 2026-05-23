"""Grok flat-manifest route for /mcp/grok (B2).

∀ client using /mcp/grok: all 65 canonical tools exposed directly,
no dispatch abstraction. Tool objects shared with /mcp via pre-prune
capture in _build_server() — same callables, different manifest shape.

Boot-time invariant: registered tool count = derive_grok_manifest() count ∨ RuntimeError.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from universal_logging import get_logger

logger = get_logger(__name__)

_CANONICAL_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "mcp" / "canonical.yaml"
)

_GROK_MANIFEST_CACHE: list[dict[str, Any]] | None = None


def get_grok_manifest(
    canonical_yaml_path: Path = _CANONICAL_PATH,
) -> list[dict[str, Any]]:
    """Return the cached grok manifest, deriving it at first call.

    ∀ calls after boot: returns the same module-level cache (init-time only).

    NOTE: The cache is keyed on first-call success, not on the path argument.
    Production always passes ``_CANONICAL_PATH``; callers that need a different
    YAML must invalidate ``_GROK_MANIFEST_CACHE`` manually or call
    ``derive_grok_manifest`` directly.
    """
    global _GROK_MANIFEST_CACHE
    if _GROK_MANIFEST_CACHE is None:
        from _derive import derive_grok_manifest  # noqa: PLC0415

        _GROK_MANIFEST_CACHE = derive_grok_manifest(canonical_yaml_path)
    return _GROK_MANIFEST_CACHE


def emit_boot_shadow_log() -> None:
    """Emit a single structured boot line for /mcp/grok manifest health.

    Logged at INFO to /tmp/logs/universal-llm-gateway/ (via universal_logging).
    Fields: tool_count, total_bytes (JSON-serialised manifest), names_sha256.
    """
    manifest = get_grok_manifest()
    names = sorted(e["canonical_name"] for e in manifest)
    names_sha256 = hashlib.sha256(json.dumps(names).encode()).hexdigest()
    total_bytes = len(json.dumps(manifest, sort_keys=True).encode())
    logger.info(
        "grok_manifest_boot tool_count=%d total_bytes=%d names_sha256=%s",
        len(manifest),
        total_bytes,
        names_sha256,
    )


async def capture_pre_prune_tools(mcp: FastMCP) -> dict[str, Any]:
    """Capture all registered Tool objects from mcp (before pruning) for the grok server.

    ∀ tool registered at call time: captured by name → Tool object.
    Call before _prune_to_primary removes non-primary tools.
    """
    return {t.name: await mcp.get_tool(t.name) for t in await mcp.list_tools()}


async def capture_post_prune_tools(mcp: FastMCP, pre_prune: dict[str, Any]) -> None:
    """Capture inline tools defined after pruning (fs, dispatch) into pre_prune.

    ∀ tool ∈ mcp ∖ pre_prune after _demote_inline_wrappers: add to pre_prune dict.
    """
    for tool in await mcp.list_tools():
        if tool.name not in pre_prune:
            pre_prune[tool.name] = await mcp.get_tool(tool.name)


def wire_grok_route(
    asgi_app: Any,
    pre_prune_tool_objects: dict[str, Any],
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]] | None,
    overflow_registry: dict[str, Any] | None,
) -> None:
    """Build /mcp/grok flat-manifest route and wire it into asgi_app (B2).

    Builds grok_mcp, verifies manifest count, emits boot shadow log, and
    mounts grok routes into asgi_app's router — including composing the
    grok app's lifespan (StreamableHTTPSessionManager) with the main app's
    lifespan so the session manager is properly initialised on startup.
    """
    from contextlib import asynccontextmanager  # noqa: PLC0415

    grok_mcp = build_grok_server(
        pre_prune_tool_objects,
        overflow_metadata=overflow_metadata or {},
        overflow_registry=overflow_registry or {},
    )
    verify_grok_manifest_count(grok_mcp)
    emit_boot_shadow_log()
    grok_asgi = grok_mcp.http_app(
        path="/mcp/grok", transport="streamable-http", stateless_http=True
    )

    # Compose grok_asgi lifespan with the main app's lifespan.
    # ∀ boot: both lifespans run → both StreamableHTTPSessionManagers initialised.
    # Without this, grok routes are reachable but their session manager is not
    # started, causing RuntimeError on first request.
    original_lifespan = asgi_app.router.lifespan_context
    grok_lifespan = grok_asgi.router.lifespan_context

    @asynccontextmanager
    async def _combined_lifespan(app: Any) -> Any:
        async with original_lifespan(app):
            async with grok_lifespan(app):
                yield

    asgi_app.router.lifespan_context = _combined_lifespan

    for route in grok_asgi.routes:
        asgi_app.router.routes.append(route)


class _CallableWrapper:
    """Minimal wrapper to expose a raw callable as a Tool-like object with .fn."""

    __slots__ = ("fn",)

    def __init__(self, fn: Any) -> None:
        self.fn = fn


def build_grok_server(
    pre_prune_tool_objects: dict[str, Any],
    *,
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]] | None = None,
    overflow_registry: dict[str, Any] | None = None,
) -> FastMCP:
    """Build grok FastMCP from tool objects captured in the main server build.

    ∀ tool_name ∈ canonical manifest (flat_call_shape.tool): register tool in grok_mcp.
    Thin dispatch wrappers route calls to the same underlying implementations as /mcp.
    Registers tool_search separately (post-prune in main server).

    All 65 tools exposed flat — no dispatch abstraction for Grok-CLI consumers.

    SYNC-ONLY CONTRACT: this function and ``verify_grok_manifest_count`` use
    ``asyncio.run(...)`` internally and MUST be called from a sync context
    (boot path, never from inside a running event loop). Moving boot into
    an async lifespan requires converting these helpers to ``async def``
    first. Mirrors the ``asyncio.run`` discipline in ``server.py::_prune_to_primary``.

    Args:
        pre_prune_tool_objects: dict of name → Tool object (pre+post prune captures).
        overflow_metadata: optional overflow metadata to pass to tool_search; defaults to {}.
        overflow_registry: optional overflow callables dict (includes 'rag' and demoted
            inline wrappers). Needed to resolve rag_* canonical tools.
    """
    import yaml  # type: ignore[import]
    from tools.grok_flat_tools import register_grok_flat_tools  # noqa: PLC0415

    grok_mcp = FastMCP("gateway-tools-grok")
    manifest = get_grok_manifest()

    # Load raw canonical.yaml for dispatcher_call_shape access.
    raw_data: dict[str, Any] = yaml.safe_load(
        _CANONICAL_PATH.read_text(encoding="utf-8")
    )

    # Merge pre_prune_tool_objects with overflow_registry callables.
    # overflow_registry contains rag and other inline tools demoted after pruning.
    effective_tools: dict[str, Any] = dict(pre_prune_tool_objects)
    for name, fn in (overflow_registry or {}).items():
        if name not in effective_tools:
            effective_tools[name] = _CallableWrapper(fn)

    registered_count, missing = register_grok_flat_tools(
        grok_mcp, effective_tools, manifest, raw_data
    )

    # Register tool_search (post-prune in main server — must be added separately).
    canonical_tool_names = {e["name"]: e for e in manifest}
    if "tool_search" in canonical_tool_names:
        from tool_search import register_tool_search_tool  # noqa: PLC0415

        register_tool_search_tool(grok_mcp, overflow_metadata or {})
        # Override schema to match canonical.yaml (FastMCP derives from Python sig which
        # includes FastMCP-added metadata like additionalProperties and defaults).
        ts_schema = canonical_tool_names["tool_search"].get("inputSchema", {})
        if ts_schema:
            import asyncio as _asyncio  # noqa: PLC0415

            async def _patch_ts() -> None:
                ts_tool = await grok_mcp.get_tool("tool_search")
                grok_mcp.local_provider.remove_tool("tool_search")
                grok_mcp.add_tool(ts_tool.model_copy(update={"parameters": ts_schema}))

            _asyncio.run(_patch_ts())
        registered_count += 1

    if missing:
        logger.warning(
            "grok_mcp built with %d/%d canonical tools; %d missing: %s",
            registered_count,
            len(manifest),
            len(missing),
            sorted(missing),
        )
    else:
        logger.info(
            "grok_mcp built: %d/%d canonical tools registered",
            registered_count,
            len(manifest),
        )

    return grok_mcp


def verify_grok_manifest_count(grok_mcp: FastMCP) -> None:
    """Assert grok_mcp registered tool count equals canonical manifest length.

    ∀ boot: len(grok_mcp.list_tools()) = len(derive_grok_manifest()) ∨ RuntimeError.
    Consistent with the Risk-4 startup pattern in server.py.

    SYNC-ONLY CONTRACT: uses ``asyncio.run(...)`` internally; call from sync
    boot context only. See ``build_grok_server`` for the discipline.
    """
    manifest = get_grok_manifest()
    expected = len(manifest)
    actual_tools = asyncio.run(grok_mcp.list_tools())
    actual = len(actual_tools)
    if actual != expected:
        actual_names = sorted(t.name for t in actual_tools)
        canonical_names = sorted(e["name"] for e in manifest)
        extra = sorted(set(actual_names) - set(canonical_names))
        missing = sorted(set(canonical_names) - set(actual_names))
        raise RuntimeError(
            f"Grok manifest count mismatch at boot: canonical.yaml says {expected} "
            f"tools but /mcp/grok has {actual} tools. "
            f"Extra: {extra}, Missing: {missing}. "
            f"Update canonical.yaml or tool registrations before starting the server."
        )
    logger.info("grok_manifest verified: %d tools match canonical.yaml", actual)
