"""Contract-scoped ``tools/list`` filtering for cursor-sdk stdio MCP bridge.

When ``ULG_MCP_CONTRACT`` is ``implement`` or ``pure-mechanical``, the stdio
bridge proxies ``fastmcp-remote`` and trims primary tool names to the
``contract_primary_domains`` allow-list from ``canonical.yaml``. Unset env
preserves the legacy ``os.execve`` path (zero behavior change).
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
    """Read one MCP stdio Content-Length framed JSON message."""
    header = b""
    while True:
        chunk = stream.read(1)
        if not chunk:
            return None
        header += chunk
        if header.endswith(b"\r\n\r\n"):
            break
    content_length = 0
    for line in header.decode("ascii", errors="replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
            break
    if content_length <= 0:
        return None
    body = stream.read(content_length)
    if len(body) < content_length:
        return None
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("MCP frame body must be a JSON object")
    return parsed


def write_framed_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """Write one MCP stdio Content-Length framed JSON message."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(body)
    stream.flush()


def _copy_upstream(
    upstream: BinaryIO,
    downstream: BinaryIO,
    *,
    allow: frozenset[str] | None,
) -> None:
    while True:
        message = read_framed_message(upstream)
        if message is None:
            break
        if allow is not None:
            message = filter_tools_list_payload(message, allow)
        write_framed_message(downstream, message)


def _copy_downstream(downstream: BinaryIO, upstream: BinaryIO) -> None:
    while True:
        message = read_framed_message(upstream)
        if message is None:
            break
        write_framed_message(downstream, message)


def run_filtered_stdio_proxy(
    *,
    child_cmd: str,
    child_args: list[str],
    child_env: dict[str, str],
    allow: frozenset[str],
) -> int:
    """Spawn *child_cmd* and bidirectionally proxy stdio with tools/list filter."""
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

    upstream_err: list[BaseException] = []

    def _upstream_worker() -> None:
        try:
            _copy_upstream(proc.stdout, sys.stdout.buffer, allow=allow)
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
        _copy_downstream(proc.stdin, sys.stdin.buffer)
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
