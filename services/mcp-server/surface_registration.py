"""Conditional MCP tool registration per endpoint surface."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from endpoint_surface import Surface
from universal_logging import get_logger

from tools._agent_bus_read import register_agent_bus_read_tool
from tools.advisor import register_advisor_tools
from tools.agent_bus import register_agent_bus_tools
from tools.browse import register_browse_tool
from tools.browser import register_browser_tools
from tools.close import register_close_tools
from tools.context import register_context_tools
from tools.cortex import register_cortex_tools
from tools.cortex_named_tools import register_cortex_named_tools
from tools.cse_session import register_cse_session_tool
from tools.cursor_request import register_cursor_request_tool
from tools.delegate import register_delegate_schema_transform, register_delegate_tools
from tools.events import register_event_tools
from tools.extract_directory import register_extract_directory_tools
from tools.extract_document import register_extract_document_tools
from tools.filesystem import register_filesystem_tools
from tools.fleet_liveness import register_fleet_liveness_tools
from tools.frontier import register_frontier_tools
from tools.frontier_imagine import register_imagine_tools
from tools.git_integrate import register_git_integrate_tools
from tools.imprint import register_imprint_tools
from tools.recall import register_recall_tools
from tools.manage import register_manage_tools
from tools.markdown_tool import register_markdown_tools
from tools.model_status import register_model_status_tools
from tools.notify import register_notify_tools
from tools.panel_dispatch import register_panel_dispatch_tools
from tools.pipeline import register_pipeline_tools
from tools.pipeline_consult import register_pipeline_consult_tools
from tools.project import register_project_tools
from tools.project_ask import register_project_ask_tool
from tools.promote_document_to_evidence import (
    register_promote_document_to_evidence_tools,
)
from tools.quality import register_quality_tools
from tools.rag import register_rag_tools
from tools.rag_articles import register_rag_article_tools
from tools.security import register_security_tools
from tools.security_js import register_security_js_tools
from tools.skill_suggest import register_skill_suggest_tools
from tools.sqlite import register_sqlite_tools
from tools.topology import register_topology_tools
from tools.trigger import register_trigger_tool
from tools.vision_digest import register_vision_digest_tools
from tools.web import register_web_tools
from tools.x_dm import register_x_dm_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def _env_truthy(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def register_tools_for_surface(mcp: FastMCP, surface: Surface) -> None:
    """Register handler callables for one dual-endpoint FastMCP instance."""
    register_filesystem_tools(mcp)
    register_markdown_tools(mcp)
    register_web_tools(mcp)
    register_x_dm_tools(mcp)
    register_browse_tool(mcp)
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
            logger.info("%s disabled (%s=false)", tool_name, env_var)

    register_extract_document_tools(mcp)
    register_promote_document_to_evidence_tools(mcp)
    register_extract_directory_tools(mcp)
    register_vision_digest_tools(mcp)
    register_agent_bus_tools(mcp)
    register_agent_bus_read_tool(mcp)
    register_cse_session_tool(mcp)
    register_cursor_request_tool(mcp)
    register_fleet_liveness_tools(mcp)
    register_cortex_tools(mcp, surface=surface)
    register_cortex_named_tools(mcp, surface=surface)
    register_advisor_tools(mcp)
    register_close_tools(mcp)
    register_skill_suggest_tools(mcp)

    if surface == "life":
        register_imprint_tools(mcp)
        register_recall_tools(mcp)
        register_delegate_tools(mcp)
        register_delegate_schema_transform(mcp)
        register_notify_tools(mcp)
        register_trigger_tool(mcp)

    if surface == "code":
        register_manage_tools(mcp)
        register_model_status_tools(mcp)
        register_topology_tools(mcp)
        register_project_tools(mcp)
        register_sqlite_tools(mcp)
        register_event_tools(mcp)
        register_pipeline_tools(mcp)
        register_pipeline_consult_tools(mcp)
        register_frontier_tools(mcp)
        register_panel_dispatch_tools(mcp)
        register_git_integrate_tools(mcp)
        register_quality_tools(mcp)
        register_imagine_tools(mcp)
        register_security_tools(mcp)
        register_security_js_tools(mcp)
        register_project_ask_tool(mcp)
        register_trigger_tool(mcp)
