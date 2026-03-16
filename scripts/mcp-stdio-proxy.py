#!/usr/bin/env python3
"""stdio → HTTP proxy for the vortex MCP server.

Cursor uses this as a `command`-type MCP server (stdio transport).
It translates MCP JSON-RPC messages from stdin to HTTP POST requests
against the real MCP server, and writes responses back to stdout.

The server returns chunked SSE with keep-alive connections, so we read
incrementally and return as soon as a complete event arrives.

Connects to MCP_URL with proper TLS hostname verification. The default
uses the public hostname (mcp.k-1.me) with /etc/hosts resolving it to
127.0.0.1 for local connections.

Config in .cursor/mcp.json:
    "vortex": {
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
from pathlib import Path

# Connect via hostname so TLS hostname verification works correctly.
# /etc/hosts resolves mcp.k-1.me → 127.0.0.1 for local connections.
MCP_URL = os.environ.get("MCP_URL", "https://mcp.k-1.me/mcp")


def _strip_quotes(value: str) -> str:
    """Strip paired outer quotes from scalar YAML values.

    Returns the input unchanged when the string is too short to contain a
    matching pair or when the outer characters are not the same quote mark.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_mcp_yaml_scalar(key: str) -> str:
    """Read a top-level scalar from ~/.gateway/mcp.yaml without PyYAML dependency."""
    mcp_yaml = Path(
        os.environ.get("MCP_YAML", str(Path.home() / ".gateway" / "mcp.yaml"))
    ).expanduser()
    if not mcp_yaml.exists():
        return ""
    try:
        for raw_line in mcp_yaml.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(f"{key}:"):
                continue
            value = line.split(":", 1)[1].split("#", 1)[0].strip()
            return _strip_quotes(value)
    except OSError as exc:
        print(
            f"mcp-stdio-proxy warning: failed reading {mcp_yaml}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return ""
    return ""


def _resolve_mcp_token() -> str:
    """Resolve MCP token from env first, then ~/.gateway/mcp.yaml auth_token."""
    from_env = os.environ.get("MCP_TOKEN", "").strip()
    if from_env:
        return from_env
    token_env_name = _read_mcp_yaml_scalar("auth_token_env").strip()
    if token_env_name:
        from_named_env = os.environ.get(token_env_name, "").strip()
        if from_named_env:
            return from_named_env
    from_yaml = _read_mcp_yaml_scalar("auth_token").strip()
    if from_yaml:
        return from_yaml
    raise RuntimeError(
        "MCP token not configured. Set MCP_TOKEN, or set auth_token_env/auth_token in ~/.gateway/mcp.yaml."
    )


def _emit_proxy_error(
    *, msg_id: object | None, is_notification: bool, exc: Exception
) -> None:
    """Report a transport failure to stderr or as a JSON-RPC error response."""
    if is_notification:
        print(
            f"mcp-stdio-proxy notification error: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return
    sys.stdout.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(exc)},
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def _post(body: bytes, *, token: str) -> str | None:
    """POST body to MCP server.

    Returns the JSON string payload for requests.
    Returns None for notifications (202 Accepted, no body).

    Reads SSE stream line-by-line, skipping heartbeat events (named
    ``event: heartbeat``), and returns as soon as a complete non-heartbeat
    event arrives.  Heartbeats keep the socket alive for long-running
    tool calls without being mistaken for the JSON-RPC response.
    """
    req = urllib.request.Request(
        MCP_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        if resp.status == 202:
            return None

        data_payload: str | None = None
        is_heartbeat = False
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event: "):
                is_heartbeat = line[7:].strip() == "heartbeat"
            elif line.startswith("data: "):
                if not is_heartbeat:
                    if data_payload is None:
                        data_payload = line[6:]
                    else:
                        data_payload += "\n" + line[6:]
            elif line == "":
                if data_payload is not None:
                    return data_payload
                is_heartbeat = False

        return data_payload or ""


def main() -> None:
    try:
        mcp_token = _resolve_mcp_token()
    except RuntimeError as exc:
        print(f"mcp-stdio-proxy startup error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from exc

    with open("/tmp/mcp-proxy-spawn.log", "a") as f:
        started = datetime.datetime.now(datetime.UTC).isoformat()
        f.write(f"{started} started pid={os.getpid()}\n")
    print(f"mcp-stdio-proxy started pid={os.getpid()}", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(
                f"mcp-stdio-proxy malformed JSON input: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue

        is_notification = "id" not in msg
        try:
            result = _post(line.encode("utf-8"), token=mcp_token)
        except Exception as exc:
            _emit_proxy_error(
                msg_id=msg.get("id"),
                is_notification=is_notification,
                exc=exc,
            )
            continue

        # JSON-RPC 2.0: notifications have no response.
        if is_notification or result is None:
            continue

        if result:
            sys.stdout.write(result + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
