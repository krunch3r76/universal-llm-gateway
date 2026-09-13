"""Stdio MCP middlebox — contract ``tools/list`` filter + steer inject.

Every nest crosses this bidirectional JSON-RPC proxy (``fastmcp-remote`` child).
When ``ULG_MCP_CONTRACT`` is ``implement`` or ``pure-mechanical``, upstream
``tools/list`` responses are trimmed to ``contract_primary_domains``. Steer
directives append as a second ``result.content`` block on ``tools/call``
responses only (``mcp_bridge_steer_inject``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MCP_SERVER_DIR = _REPO_ROOT / "services" / "mcp-server"
if str(_MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER_DIR))

from endpoint_surface import derive_contract_primary_tools  # noqa: E402

from scripts.mcp_bridge_steer_inject import (  # noqa: E402
    CURSOR_SDK_DISPATCH_ID_ENV,
    append_directive,
    claim_pending,
    mark_delivered,
)

ULG_MCP_CONTRACT_ENV = "ULG_MCP_CONTRACT"
FILTERED_CONTRACTS: frozenset[str] = frozenset({"implement", "pure-mechanical"})
_LIFE_ONLY_TOOLS: frozenset[str] = frozenset({"imprint", "recall", "delegate", "notify"})
_HIDDEN_FROM_IMPLEMENT: frozenset[str] = frozenset(
    {
        "rag",
        "retrieve",
        "cursor_request",
        "pipeline",
        "team_dispatch",
        "panel_dispatch",
    }
)


def contract_from_env(env: dict[str, str] | None = None) -> str:
    """Return normalized ``ULG_MCP_CONTRACT`` value (may be empty)."""
    source = env if env is not None else os.environ
    return (source.get(ULG_MCP_CONTRACT_ENV) or "").strip().lower()


def should_filter_stdio(env: dict[str, str] | None = None) -> bool:
    """True when the bridge must proxy and filter instead of execve."""
    return contract_from_env(env) in FILTERED_CONTRACTS


def resolve_contract_allow_list(
    contract: str,
    *,
    canonical_yaml_path: Path | None = None,
) -> frozenset[str]:
    """Derive dispatcher tool names permitted for *contract*."""
    return derive_contract_primary_tools(contract, canonical_yaml_path)


def filter_tools_list_payload(
    payload: dict[str, Any],
    allow: frozenset[str],
) -> dict[str, Any]:
    """Return *payload* with ``result.tools`` trimmed to *allow* names."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    tools = result.get("tools")
    if not isinstance(tools, list):
        return payload
    filtered = [
        tool
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name", "")) in allow
    ]
    if filtered is tools:
        return payload
    return {**payload, "result": {**result, "tools": filtered}}


def read_framed_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read one MCP stdio message.

    MCP stdio transport is newline-delimited JSON (one object per line), not
    LSP ``Content-Length`` framing — Cursor and ``fastmcp-remote`` both speak
    NDJSON, so a header-based reader blocks forever and the handshake never
    completes. Blank lines are skipped; EOF returns ``None``.
    """
    while True:
        line = stream.readline()
        if not line:
            return None
        if not line.strip():
            continue
        parsed = json.loads(line.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("MCP stdio message must be a JSON object")
        return parsed


def write_framed_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """Write one MCP stdio message as a single NDJSON line."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    stream.write(body)
    stream.write(b"\n")
    stream.flush()


def _is_tools_call_result(
    message: dict[str, Any],
    pending_methods: dict[Any, str],
) -> bool:
    msg_id = message.get("id")
    if msg_id is None or "method" in message:
        return False
    if pending_methods.get(msg_id) != "tools/call":
        return False
    if message.get("error"):
        return False
    result = message.get("result")
    if not isinstance(result, dict):
        return False
    if result.get("isError") is True:
        return False
    content = result.get("content")
    return isinstance(content, list) and bool(content)


def _maybe_inject_steer(message: dict[str, Any], pending_methods: dict[Any, str]) -> dict[str, Any]:
    if not _is_tools_call_result(message, pending_methods):
        return message
    dispatch_id = os.environ.get(CURSOR_SDK_DISPATCH_ID_ENV, "").strip()
    if not dispatch_id:
        return message
    pending = claim_pending(dispatch_id)
    if pending is None:
        return message
    message = append_directive(message, pending)
    mark_delivered(pending)
    return message


def _copy_upstream(
    upstream: BinaryIO,
    downstream: BinaryIO,
    *,
    allow: frozenset[str] | None,
    pending_methods: dict[Any, str],
) -> None:
    while True:
        message = read_framed_message(upstream)
        if message is None:
            break
        if allow is not None:
            message = filter_tools_list_payload(message, allow)
        message = _maybe_inject_steer(message, pending_methods)
        write_framed_message(downstream, message)


def _copy_downstream(
    downstream: BinaryIO,
    upstream: BinaryIO,
    *,
    pending_methods: dict[Any, str],
) -> None:
    while True:
        message = read_framed_message(upstream)
        if message is None:
            break
        msg_id = message.get("id")
        method = message.get("method")
        if msg_id is not None and isinstance(method, str):
            pending_methods[msg_id] = method
        write_framed_message(downstream, message)


def run_filtered_stdio_proxy(
    *,
    child_cmd: str,
    child_args: list[str],
    child_env: dict[str, str],
    allow: frozenset[str] | None,
) -> int:
    """Spawn *child_cmd* and bidirectionally proxy stdio with optional list filter."""
    proc = subprocess.Popen(
        [child_cmd, *child_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        env=child_env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    import threading

    pending_methods: dict[Any, str] = {}
    upstream_err: list[BaseException] = []

    def _upstream_worker() -> None:
        try:
            _copy_upstream(
                proc.stdout,
                sys.stdout.buffer,
                allow=allow,
                pending_methods=pending_methods,
            )
        except BaseException as exc:  # noqa: BLE001
            upstream_err.append(exc)
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass

    thread = threading.Thread(target=_upstream_worker, name="mcp-bridge-upstream")
    thread.start()
    try:
        _copy_downstream(
            proc.stdin,
            sys.stdin.buffer,
            pending_methods=pending_methods,
        )
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        thread.join()
    proc.wait()
    if upstream_err:
        raise upstream_err[0]
    return proc.returncode or 0
