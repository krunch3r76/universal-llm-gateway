"""MCP web_tab_read — thin relay to web_tab category pipelines (eyes: status, probe)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from mcp_events import record
from request_profile import current_request_metadata
from web_tab.mcp_registry import (
    WEB_TAB_READ_OP_TO_PIPELINE,
    WEB_TAB_READ_PIPELINE_CATEGORY,
    advertised_read_ops,
)

from tools.pipeline import _pipeline_run

if TYPE_CHECKING:
    from fastmcp import FastMCP

_INSTALLED_CACHE: tuple[float, frozenset[str]] | None = None
_CACHE_TTL_S = 60.0


class RegistryUnavailableError(Exception):
    """Stargate category listing failed or was not a pipeline members object.

    Failures are not cached. Callers must not treat this as an empty install.
    """


def clear_installed_pipeline_cache() -> None:
    """Drop a successful listing so the next read hits Stargate again."""
    global _INSTALLED_CACHE
    _INSTALLED_CACHE = None


def _load_pipeline_list() -> Any:
    from tools.pipeline import _pipeline_list

    return _pipeline_list


def _ids_from_production_listing(resp: Any) -> frozenset[str]:
    """Parse ``GET /api/v1/capabilities?category=web_tab`` members object.

    Production summaries are dicts keyed by pipeline id and include
    ``category`` and ``url``. A list, an error body, or a missing summary
    field is unavailable — not an empty install.
    """
    if not isinstance(resp, dict) or resp.get("error"):
        raise RegistryUnavailableError("registry_error")
    members = resp.get("members")
    if not isinstance(members, dict):
        raise RegistryUnavailableError("members_not_object")
    installed: set[str] = set()
    for pid, meta in members.items():
        if not isinstance(pid, str) or not isinstance(meta, dict):
            raise RegistryUnavailableError("member_shape")
        if "category" not in meta or "url" not in meta:
            raise RegistryUnavailableError("summary_shape")
        if meta.get("category") == WEB_TAB_READ_PIPELINE_CATEGORY:
            installed.add(pid)
    return frozenset(installed)


def _fetch_installed_from_stargate() -> frozenset[str]:
    try:
        list_fn = _load_pipeline_list()
    except ImportError as exc:
        raise RegistryUnavailableError("pipeline_import") from exc
    try:
        resp = list_fn(WEB_TAB_READ_PIPELINE_CATEGORY)
    except Exception as exc:
        raise RegistryUnavailableError(type(exc).__name__) from exc
    return _ids_from_production_listing(resp)


def installed_web_tab_pipeline_ids() -> frozenset[str]:
    """Return cached successful web_tab pipeline ids, querying on miss.

    Only a parsed production listing is cached. ``RegistryUnavailableError`` leaves
    the previous cache untouched when the TTL has already expired (the fetch
    ran), and a cached success is returned only inside the TTL.
    """
    global _INSTALLED_CACHE
    now = time.monotonic()
    if _INSTALLED_CACHE is not None and now - _INSTALLED_CACHE[0] < _CACHE_TTL_S:
        return _INSTALLED_CACHE[1]
    ids = _fetch_installed_from_stargate()
    _INSTALLED_CACHE = (now, ids)
    return ids


def _resolve_pipeline(op: str, installed: frozenset[str]) -> str | None:
    allowed = advertised_read_ops(installed_pipeline_ids=installed)
    if op not in allowed:
        return None
    return WEB_TAB_READ_OP_TO_PIPELINE[op]


def _stamp_relay_options(base: dict[str, Any]) -> dict[str, Any]:
    """Copy server request metadata onto relay options.

    ``surface`` on the MCP request is the mount (for example ``code``), not
    the product surface. It is stamped as ``mcp_surface`` and is not written
    into ``surface``. Product surface stays the explicit tool argument, or
    the pipeline default ``chatgpt`` when that argument is omitted.
    """
    meta = current_request_metadata() or {}
    stamped = dict(base)
    for key in ("caller_identity", "oauth_client_id"):
        val = meta.get(key)
        if val is not None and key not in stamped:
            stamped[key] = val
    oauth = meta.get("oauth_client_id") or meta.get("mcp_caller_identity")
    if oauth is not None and "oauth_client_id" not in stamped:
        stamped["oauth_client_id"] = oauth
    mount = meta.get("surface")
    if mount is not None and "mcp_surface" not in stamped:
        stamped["mcp_surface"] = mount
    return stamped


def register_web_tab_read_tool(mcp: FastMCP) -> None:
    """Register read-only web_tab MCP dispatcher."""

    @mcp.tool(title="Web tab (read)")
    def web_tab_read(
        op: Literal["status", "probe"],
        house_id: str | None = None,
        agent: str | None = None,
        target_id: str | None = None,
        cdp_url: str | None = None,
        surface: str | None = None,
    ) -> dict[str, Any]:
        """Read-only web-tab status and probe — relays to installed ``web_tab`` pipelines.

        No navigation, bind, paste, or repair. Op enum matches pipelines in category
        ``web_tab`` that are loaded on Stargate (status → ``web-tab-status``,
        probe → ``web-tab-probe``). Omitted ``surface`` is the product default
        in the pipeline, not the MCP mount surface.
        """
        try:
            installed = installed_web_tab_pipeline_ids()
        except RegistryUnavailableError as exc:
            return {
                "error": {
                    "code": "registry_unavailable",
                    "message": str(exc),
                }
            }
        pipeline_id = _resolve_pipeline(op, installed)
        if pipeline_id is None:
            return {
                "error": {
                    "code": "op_not_loaded",
                    "message": f"web_tab_read op {op!r} is not installed",
                    "advertised_ops": sorted(
                        advertised_read_ops(installed_pipeline_ids=installed)
                    ),
                }
            }
        options: dict[str, Any] = _stamp_relay_options(
            {
                k: v
                for k, v in {
                    "house_id": house_id,
                    "agent": agent,
                    "target_id": target_id,
                    "cdp_url": cdp_url,
                    "surface": surface,
                }.items()
                if v is not None
            }
        )
        record("mcp.web_tab_read.called", op=op, pipeline_id=pipeline_id)
        return _pipeline_run(
            pipeline_id,
            messages=[{"role": "user", "content": op}],
            options=options or None,
            timeout=None,
        )
