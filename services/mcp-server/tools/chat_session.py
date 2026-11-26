"""MCP chat_session tool — thin httpx relay to Jupiter CDP-ask chat-session routes.

Ops: harvest (read), probe (read), observe (read), paste (write). Product-chat URLs only —
grok.com and claude.ai/chat. No claude_bundles, web_chat_relay, or chat_harvest
imports — relay only per [universal:mcp].
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp_events import record

from tools.relay import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_chat_session_tool(mcp: FastMCP) -> None:
    """Register the chat_session relay on both /mcp/life and /mcp/code."""

    @mcp.tool(title="Chat Session")
    def chat_session(
        op: Literal["harvest", "probe", "observe", "paste"],
        url: str | None = None,
        site: Literal["grok", "claude"] | None = None,
        metadata_only: bool = False,
        include_turns: Literal["none", "last", "range"] = "none",
        limit: int = 10,
        after_turn: int | None = None,
        supersede: bool = False,
        cdp_url: str | None = None,
        prompt_text: str | None = None,
        prompt_uri: str | None = None,
        prompt_path: str | None = None,
        grant: Literal["explicit", "operator"] | None = None,
        wait_reply: bool = False,
        match_terms: list[str] | None = None,
        max_nodes: int | None = None,
        open_on_demand: bool | None = None,
    ) -> dict[str, Any]:
        """Product-chat harvest, metadata probe, DOM observe, and grant-gated paste.

        Targets grok.com and claude.ai/chat URLs via ``/v1/chat-session/*``.
        The ``observe`` op returns page text under ``page_text`` keys as untrusted content.
        Cowork CSE URLs must use ``cse_session`` instead.
        """
        if op == "harvest":
            body = {
                k: v
                for k, v in {
                    "url": url,
                    "site": site,
                    "metadata_only": metadata_only,
                    "include_turns": include_turns,
                    "limit": limit,
                    "after_turn": after_turn,
                    "supersede": supersede,
                    "cdp_url": cdp_url,
                }.items()
                if v is not None and v != ""
            }
            result = _relay("POST", "/v1/chat-session/harvest", json_body=body)
            record(
                "mcp.chat_session.harvest",
                outcome=result.get("outcome"),
                site=result.get("site"),
            )
            return result

        if op == "probe":
            body = {
                k: v
                for k, v in {
                    "url": url,
                    "site": site,
                    "metadata_only": metadata_only,
                    "include_turns": include_turns,
                    "limit": limit,
                    "after_turn": after_turn,
                    "supersede": supersede,
                    "cdp_url": cdp_url,
                }.items()
                if v is not None and v != ""
            }
            return _relay("POST", "/v1/chat-session/probe", json_body=body)

        if op == "observe":
            body = {
                k: v
                for k, v in {
                    "url": url,
                    "site": site,
                    "cdp_url": cdp_url,
                    "match_terms": match_terms,
                    "max_nodes": max_nodes,
                    "open_on_demand": open_on_demand,
                }.items()
                if v is not None and v != ""
            }
            return _relay("POST", "/v1/chat-session/observe", json_body=body)

        body = {
            k: v
            for k, v in {
                "url": url,
                "site": site,
                "prompt_text": prompt_text,
                "prompt_uri": prompt_uri,
                "prompt_path": prompt_path,
                "grant": grant,
                "cdp_url": cdp_url,
                "wait_reply": wait_reply,
            }.items()
            if v is not None and v != "" and v is not False
        }
        result = _relay("POST", "/v1/chat-session/paste", json_body=body)
        record(
            "mcp.chat_session.paste",
            ok=result.get("ok"),
            site=result.get("site"),
        )
        return result
