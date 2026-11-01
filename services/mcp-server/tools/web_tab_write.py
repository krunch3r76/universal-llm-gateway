"""MCP web_tab_write — thin relay to web-tab-write pipeline (actuation slice)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp_events import record
from web_tab.mcp_registry import (
    WEB_TAB_WRITE_OP_TO_PIPELINE,
    advertised_write_ops,
)

from tools.pipeline import _pipeline_run
from tools.web_tab_read import (
    RegistryUnavailableError,
    _stamp_relay_options,
    installed_web_tab_pipeline_ids,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

WriteOp = Literal[
    "open", "bind", "paste", "submit", "verify_committed", "read_reply"
]


def _resolve_write_pipeline(op: WriteOp, installed: frozenset[str]) -> str | None:
    allowed = advertised_write_ops(installed_pipeline_ids=installed)
    if op not in allowed:
        return None
    return WEB_TAB_WRITE_OP_TO_PIPELINE[op]


def register_web_tab_write_tool(mcp: FastMCP) -> None:
    """Register grant-gated web_tab write MCP dispatcher."""

    @mcp.tool(title="Web tab (write)")
    def web_tab_write(
        op: WriteOp,
        cdp_url: str,
        target_id: str | None = None,
        url: str | None = None,
        conversation_url: str | None = None,
        text: str | None = None,
        grant: Literal["explicit", "operator"] | None = None,
        surface: str | None = None,
        submit: bool = False,
        rebind: bool = False,
        house_id: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Actuation on product chat tabs — open, bind, paste, submit, verify, read_reply.

        Mutating ops require ``grant`` of ``explicit`` or ``operator``. Relays to
        pipeline ``web-tab-write`` when installed in category ``web_tab``.
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
        pipeline_id = _resolve_write_pipeline(op, installed)
        if pipeline_id is None:
            return {
                "error": {
                    "code": "op_not_loaded",
                    "message": f"web_tab_write op {op!r} is not installed",
                    "advertised_ops": sorted(
                        advertised_write_ops(installed_pipeline_ids=installed)
                    ),
                }
            }
        options: dict[str, Any] = _stamp_relay_options(
            {
                k: v
                for k, v in {
                    "op": op,
                    "house_id": house_id,
                    "agent": agent,
                    "target_id": target_id,
                    "cdp_url": cdp_url,
                    "url": url,
                    "conversation_url": conversation_url,
                    "text": text,
                    "grant": grant,
                    "surface": surface,
                    "submit": submit,
                    "rebind": rebind,
                }.items()
                if v is not None and v is not False
            }
        )
        options["op"] = op
        record("mcp.web_tab_write.called", op=op, pipeline_id=pipeline_id)
        return _pipeline_run(
            pipeline_id,
            messages=[{"role": "user", "content": op}],
            options=options,
            timeout=None,
        )
