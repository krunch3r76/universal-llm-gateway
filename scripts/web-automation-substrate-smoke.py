#!/usr/bin/env python3
"""Phase 0 substrate smoke — headless vortex MCP auth + agent_bus closeout.

Proves the web-automation service token can reach vortex MCP and post a bus
reply before web-generate admission opens (Track 1 gate).

Env (canonical):
  WEB_AUTOMATION_MCP_TOKEN — bearer for vortex MCP
  VORTEX_MCP_URL           — streamable HTTP MCP endpoint

Local dev fallbacks (documented only; production worker must set canonical names):
  MCP_AUTH_TOKEN, MCP_PUBLIC_URL / MCP_URL

Exit 0 on green smoke; non-zero with structured JSON error on auth/config failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

_JSONRPC_VERSION = "2.0"
_SMOKE_FROM_AGENT = "claude-web-auto"
_SMOKE_TO_AGENT = "dispatch"


def _parse_sse_json(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _jsonrpc(method: str, params: dict[str, Any], req_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": _JSONRPC_VERSION,
        "method": method,
        "params": params,
        "id": req_id,
    }


def _read_gateway_mcp_yaml() -> dict[str, Any]:
    yaml_path = Path.home() / ".gateway" / "mcp.yaml"
    if not yaml_path.is_file():
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def resolve_env() -> tuple[str, str, dict[str, str]]:
    """Return (token, url, provenance). Raises SystemExit on missing config."""
    cfg = _read_gateway_mcp_yaml()
    token = os.environ.get("WEB_AUTOMATION_MCP_TOKEN", "").strip()
    token_source = "WEB_AUTOMATION_MCP_TOKEN"
    if not token:
        token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        token_source = "MCP_AUTH_TOKEN"
    if not token:
        env_key = str(cfg.get("mcp_auth_token_env", "")).strip()
        if env_key:
            token = os.environ.get(env_key, "").strip()
            if token:
                token_source = f"env:{env_key}"
    if not token:
        token = str(cfg.get("auth_token", "")).strip()
        if token:
            token_source = "yaml:auth_token"
    url = os.environ.get("VORTEX_MCP_URL", "").strip()
    url_source = "VORTEX_MCP_URL"
    if not url:
        url = os.environ.get("MCP_PUBLIC_URL", "").strip()
        if url:
            url_source = "MCP_PUBLIC_URL"
    if not url:
        url = os.environ.get("MCP_URL", "").strip()
        if url:
            url_source = "MCP_URL"
    if not url:
        url = str(cfg.get("mcp_server_url", "")).strip()
        if url:
            url_source = "yaml:mcp_server_url"
    if not url:
        url = "https://mcp.k-1.me/mcp"
        url_source = "default"
    if not url.rstrip("/").endswith("/mcp"):
        url = f"{url.rstrip('/')}/mcp"
    if not token:
        _fail(
            "WEB_AUTH_CONFIG",
            "MCP token missing — set WEB_AUTOMATION_MCP_TOKEN, MCP_AUTH_TOKEN, "
            "or ~/.gateway/mcp.yaml auth_token",
            {"token_source": token_source},
        )
    return token, url, {"token_source": token_source, "url_source": url_source}


def _fail(code: str, message: str, detail: dict[str, Any] | None = None) -> None:
    payload = {"ok": False, "error_code": code, "message": message}
    if detail:
        payload["detail"] = detail
    print(json.dumps(payload, indent=2))
    raise SystemExit(1)


async def mcp_call(
    client: httpx.AsyncClient,
    *,
    url: str,
    token: str,
    tool: str,
    arguments: dict[str, Any],
    req_id: int,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        url,
        json=_jsonrpc("tools/call", {"name": tool, "arguments": arguments}, req_id),
        headers=headers,
    )
    if resp.status_code == 401:
        _fail(
            "WEB_AUTH_CONFIG",
            "MCP authentication rejected (401)",
            {"url": url},
        )
    resp.raise_for_status()
    body = _parse_sse_json(resp.text)
    if body.get("error"):
        _fail(
            "WEB_MCP_TOOL_ERROR",
            f"MCP tools/call failed: {body['error']}",
            {"tool": tool, "arguments": arguments},
        )
    result = body.get("result", {})
    content = result.get("content", [])
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    merged = "\n".join(t for t in texts if t).strip()
    if not merged:
        return {"raw": result}
    try:
        return json.loads(merged)
    except json.JSONDecodeError:
        return {"text": merged}


async def run_smoke(*, dry_run: bool) -> dict[str, Any]:
    token, url, provenance = resolve_env()
    if dry_run:
        return {"ok": True, "dry_run": True, "url": url, **provenance}

    slug = f"web-p0-substrate-smoke-{int(time.time())}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0),
    ) as client:
        listed = await mcp_call(
            client,
            url=url,
            token=token,
            tool="agent_bus",
            arguments={
                "tool": "threads",
                "arguments": json.dumps({"status": "active", "limit": 1}),
            },
            req_id=1,
        )
        if listed.get("error"):
            _fail("WEB_MCP_SESSION", "agent_bus unreachable via MCP", listed)

        post_args = {
            "tool": "post",
            "arguments": json.dumps(
                {
                    "slug": slug,
                    "to": _SMOKE_TO_AGENT,
                    "subject": "P0 substrate smoke — admit",
                    "body": (
                        "Phase 0 smoke: headless MCP session admitted.\n"
                        "Await closeout from claude-web-auto."
                    ),
                    "from_agent": "web-automation-smoke",
                    "tags": [
                        "project:ulg",
                        "type:smoke",
                        "project:web-generate-substrate",
                    ],
                }
            ),
        }
        post_result = await mcp_call(
            client,
            url=url,
            token=token,
            tool="agent_bus",
            arguments=post_args,
            req_id=2,
        )
        thread_raw = (
            post_result.get("thread")
            or post_result.get("thread_id")
            or post_result.get("id")
        )
        if isinstance(thread_raw, dict):
            thread_id = str(thread_raw.get("id", "")).strip()
            turn_number = (
                thread_raw.get("turn_count") or post_result.get("turn_number") or 1
            )
        else:
            thread_id = str(thread_raw or "").strip()
            turn_number = post_result.get("turn_number") or 1
        if not thread_id:
            _fail("WEB_SMOKE_POST", "post did not return thread id", post_result)

        reply_args = {
            "tool": "reply",
            "arguments": json.dumps(
                {
                    "thread": thread_id,
                    "to": _SMOKE_TO_AGENT,
                    "subject": "P0 substrate smoke — closeout",
                    "body": (
                        "## Closeout\n\n"
                        "**status:** complete\n\n"
                        "Headless vortex MCP session authenticated and posted bus closeout.\n"
                        "Gate: web-generate P1 admission may proceed to densify."
                    ),
                    "after_turn": turn_number,
                    "from_agent": _SMOKE_FROM_AGENT,
                    "close": True,
                }
            ),
        }
        reply_result = await mcp_call(
            client,
            url=url,
            token=token,
            tool="agent_bus",
            arguments=reply_args,
            req_id=3,
        )
        if reply_result.get("error"):
            _fail("WEB_SMOKE_REPLY", "closeout reply failed", reply_result)

        fetch_result = await mcp_call(
            client,
            url=url,
            token=token,
            tool="agent_bus",
            arguments={
                "tool": "fetch",
                "arguments": json.dumps({"thread": thread_id, "last": 5}),
            },
            req_id=4,
        )
        turns = fetch_result.get("turns") or []
        turn_count = len(turns) if isinstance(turns, list) else 0
        if turn_count < 2:
            _fail(
                "WEB_SMOKE_VERIFY",
                f"expected turn 2 closeout, got {turn_count} turns",
                {"thread": thread_id, "fetch": fetch_result},
            )

        return {
            "ok": True,
            "thread_id": thread_id,
            "turn_count": turn_count,
            "from_agent": _SMOKE_FROM_AGENT,
            "mcp_url": url,
            **provenance,
            "post": post_result,
            "reply": reply_result,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate env only; do not call MCP or agent_bus",
    )
    args = parser.parse_args()
    result = asyncio.run(run_smoke(dry_run=args.dry_run))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
