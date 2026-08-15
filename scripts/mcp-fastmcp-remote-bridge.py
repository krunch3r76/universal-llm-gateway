#!/usr/bin/env python3
"""Thin stdio bridge to vortex MCP via ``fastmcp-remote`` (Track 3).

Replaces the deprecated custom ``mcp-stdio-proxy.py`` for cursor-sdk dispatches.
Upstream FastMCP handles streamable-HTTP proxying; this launcher resolves
auth/URL, emits startup telemetry, and execs ``fastmcp-remote``.

When ``ULG_MCP_CONTRACT`` is ``implement`` or ``pure-mechanical``, spawns
``fastmcp-remote`` as a child and filters ``tools/list`` primary names instead
of execve (G5 lead-kit surface scoping).
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.mcp_bridge_contract_filter import (  # noqa: E402
    resolve_contract_allow_list,
    run_filtered_stdio_proxy,
    should_filter_stdio,
)
from services.git_integration_worker.cursor_sdk_context import (  # noqa: E402
    CursorSdkParityError,
    resolve_fastmcp_remote_cmd,
    resolve_mcp_bridge,
    resolve_mcp_token,
)

DEFAULT_MCP_URL = "https://mcp.k-1.me/mcp/code"
_EVENTS_SOCK = os.environ.get(
    "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
)
_EVENTS_ENABLED = os.environ.get("MCP_PROXY_EVENTS", "true").lower() in {
    "true",
    "1",
    "yes",
}
_EVENT_SOURCE = "mcp-fastmcp-remote-bridge"
_BRIDGE_START_SIGNAL = "mcp.bridge.stdio.started"


def _emit_event_sync(sock_path: str, signal: str, **payload: object) -> None:
    """Best-effort synchronous UDS publish — must finish before ``os.execve``."""
    now = datetime.datetime.now(datetime.UTC)
    line = (
        json.dumps(
            {
                "signal": signal,
                "source": _EVENT_SOURCE,
                "role": "observation",
                "scope": "global",
                "timestamp": now.isoformat(),
                "ts_unix_ms": int(now.timestamp() * 1000),
                "payload": {"pid": os.getpid(), **payload},
            },
            default=str,
        )
        + "\n"
    )
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(sock_path)
        sock.sendall(line.encode())
    except OSError as exc:
        print(
            f"{_EVENT_SOURCE}: startup event publish failed: {exc!r}",
            file=sys.stderr,
            flush=True,
        )
    finally:
        if sock is not None:
            sock.close()


def _emit_startup(*, mcp_url: str, bridge_cmd: str, filtered: bool = False) -> None:
    if not _EVENTS_ENABLED:
        return
    _emit_event_sync(
        _EVENTS_SOCK,
        _BRIDGE_START_SIGNAL,
        transport="stdio",
        bridge="fastmcp-remote",
        bridge_cmd=bridge_cmd,
        mcp_url=mcp_url,
        contract_filter=filtered,
    )


def _fastmcp_argv(*, bridge_cmd: str, mcp_url: str) -> list[str]:
    return [
        bridge_cmd,
        mcp_url,
        "--auth",
        "none",
        "--header",
        "Authorization: Bearer ${MCP_TOKEN}",
        "--silent",
    ]


def _run_execve(*, bridge_cmd: str, mcp_url: str, env: dict[str, str]) -> None:
    os.execve(bridge_cmd, _fastmcp_argv(bridge_cmd=bridge_cmd, mcp_url=mcp_url), env)


def main() -> None:
    try:
        resolve_mcp_bridge(_REPO_ROOT)
    except CursorSdkParityError as exc:
        print(f"mcp-fastmcp-remote-bridge: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from exc

    token, _source = resolve_mcp_token()
    last_home = Path.home()
    if not token:
        # Dispatch HOME isolation copies mcp.json but historically omitted
        # ~/.gateway/mcp.yaml, so Path.home() lookup fails closed. Fall back
        # to the operator login home — same source GIW's build_mcp_servers uses.
        from services.git_integration_worker.cursor_home import operator_real_home

        last_home = operator_real_home()
        token, _source = resolve_mcp_token(real_home=last_home)
    if not token:
        from services.git_integration_worker.cursor_home import observed_home_kind

        kind = observed_home_kind(last_home)
        print(
            "mcp-fastmcp-remote-bridge: MCP token not configured "
            f"(MCP_TOKEN / ~/.gateway/mcp.yaml) observed_home_kind={kind} "
            f"observed_home={last_home}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)

    mcp_url = os.environ.get("MCP_URL", DEFAULT_MCP_URL).strip() or DEFAULT_MCP_URL
    try:
        bridge_cmd = resolve_fastmcp_remote_cmd()
    except CursorSdkParityError as exc:
        print(f"mcp-fastmcp-remote-bridge: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from exc

    env = os.environ.copy()
    env["MCP_TOKEN"] = token

    if should_filter_stdio(env):
        contract = (env.get("ULG_MCP_CONTRACT") or "").strip().lower()
        allow = resolve_contract_allow_list(contract)
        _emit_startup(mcp_url=mcp_url, bridge_cmd=bridge_cmd, filtered=True)
        rc = run_filtered_stdio_proxy(
            child_cmd=bridge_cmd,
            child_args=_fastmcp_argv(bridge_cmd=bridge_cmd, mcp_url=mcp_url)[1:],
            child_env=env,
            allow=allow,
        )
        raise SystemExit(rc)

    _emit_startup(mcp_url=mcp_url, bridge_cmd=bridge_cmd, filtered=False)
    _run_execve(bridge_cmd=bridge_cmd, mcp_url=mcp_url, env=env)


if __name__ == "__main__":
    main()
