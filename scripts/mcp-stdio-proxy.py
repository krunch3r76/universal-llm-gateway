#!/usr/bin/env python3
"""stdio → HTTP proxy for the universal-gateway MCP server.

Cursor uses this as a `command`-type MCP server (stdio transport).
It translates MCP JSON-RPC messages from stdin to HTTP POST requests
against the real MCP server, and writes responses back to stdout.

The server returns chunked SSE with keep-alive connections, so we read
incrementally and return as soon as a complete event arrives.

Connects to MCP_URL with proper TLS hostname verification. The default
uses the public hostname (mcp.k-1.me) with /etc/hosts resolving it to
127.0.0.1 for local connections.

Config in .cursor/mcp.json:
    "universal-gateway": {
        "command": "python3",
        "args": ["/mnt/torus/projects/universal-llm-gateway/scripts/mcp-stdio-proxy.py"]
    }
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request

# Connect via hostname so TLS hostname verification works correctly.
# /etc/hosts resolves mcp.k-1.me → 127.0.0.1 for local connections.
MCP_URL = os.environ.get("MCP_URL", "https://mcp.k-1.me/mcp")
MCP_TOKEN = os.environ.get(
    "MCP_TOKEN",
    "c6df64a99d24a17e88104497fc21543e4f0d09fbe748bbebaf728ab93f0fa6cc",
)


def _post(body: bytes) -> str | None:
    """POST body to MCP server.

    Returns the JSON string payload for requests.
    Returns None for notifications (202 Accepted, no body).

    Reads SSE stream line-by-line and returns as soon as a complete
    event arrives — never waits for the keep-alive connection to close.
    """
    req = urllib.request.Request(
        MCP_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {MCP_TOKEN}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status == 202:
            return None

        # Read SSE stream incrementally. An event ends with a blank line
        # after a `data: <json>` line. Return immediately on completion —
        # exiting the `with` block closes the connection.
        data_payload: str | None = None
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("data: "):
                data_payload = line[6:]
            elif line == "" and data_payload is not None:
                return data_payload  # complete SSE event

        # Fallback: plain JSON response (not SSE), or truncated stream
        return data_payload or ""


def main() -> None:
    with open("/tmp/mcp-proxy-spawn.log", "a") as f:
        f.write(f"{datetime.datetime.utcnow().isoformat()} started pid={os.getpid()}\n")
    print(f"mcp-stdio-proxy started pid={os.getpid()}", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        is_notification = "id" not in msg
        try:
            result = _post(line.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            if is_notification:
                print(f"mcp-stdio-proxy notification error: {exc}", file=sys.stderr, flush=True)
                continue
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": str(exc)},
            }) + "\n")
            sys.stdout.flush()
            continue

        # JSON-RPC 2.0: notifications have no response.
        if is_notification or result is None:
            continue

        if result:
            sys.stdout.write(result + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
