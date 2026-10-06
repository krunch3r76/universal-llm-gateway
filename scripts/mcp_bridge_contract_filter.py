"""Stdio MCP middlebox — contract ``tools/list`` filter + steer inject.

Every nest crosses this bidirectional JSON-RPC proxy (``fastmcp-remote`` child).
When ``ULG_MCP_CONTRACT`` is ``implement`` or ``pure-mechanical``, upstream
``tools/list`` responses are trimmed to ``contract_primary_domains``. Those
seats may call ``team_dispatch`` only in the G6 review shape
(``libs/g6_review_class.py``); every other ``team_dispatch`` tools/call is
refused here and never forwarded. Steer directives append as a second
``result.content`` block on ``tools/call`` responses only
(``mcp_bridge_steer_inject``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MCP_SERVER_DIR = _REPO_ROOT / "services" / "mcp-server"
_LIBS_DIR = _REPO_ROOT / "libs"
for _path in (_LIBS_DIR, _MCP_SERVER_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from endpoint_surface import derive_contract_primary_tools  # noqa: E402
from g6_review_class import refuse_filtered_tools_call  # noqa: E402

from scripts.mcp_bridge_generate_ledger import GenerateObserver  # noqa: E402
from scripts.mcp_bridge_steer_inject import (  # noqa: E402
    CURSOR_SDK_DISPATCH_ID_ENV,
    append_steer_text,
    consume_next_steer_envelope,
)

ULG_MCP_CONTRACT_ENV = "ULG_MCP_CONTRACT"
ULG_DISPATCH_THREAD_ENV = "ULG_DISPATCH_THREAD_ID"
FILTERED_CONTRACTS: frozenset[str] = frozenset({"implement", "pure-mechanical"})
_LIFE_ONLY_TOOLS: frozenset[str] = frozenset(
    {"imprint", "recall", "delegate", "notify"}
)
_HIDDEN_FROM_IMPLEMENT: frozenset[str] = frozenset(
    {
        # Not the call gate. Enforcement is refuse_filtered_tools_call plus
        # contract_primary_domains. This set is unused documentation of names
        # the implement allow list still omits.
        "rag",
        "retrieve",
        "cursor_request",
        "pipeline",
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
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
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


def _maybe_inject_steer(
    message: dict[str, Any], pending_methods: dict[Any, str]
) -> dict[str, Any]:
    if not _is_tools_call_result(message, pending_methods):
        return message
    dispatch_id = os.environ.get(CURSOR_SDK_DISPATCH_ID_ENV, "").strip()
    if not dispatch_id:
        return message
    envelope = consume_next_steer_envelope(dispatch_id)
    if not envelope:
        return message
    return append_steer_text(message, envelope)


def _copy_upstream(
    upstream: BinaryIO,
    downstream: BinaryIO,
    *,
    allow: frozenset[str] | None,
    pending_methods: dict[Any, str],
    client_out_lock: threading.Lock | None = None,
    observer: GenerateObserver | None = None,
) -> None:
    while True:
        message = read_framed_message(upstream)
        if message is None:
            break
        if allow is not None:
            message = filter_tools_list_payload(message, allow)
        if observer is not None:
            observer.on_result(message)
        message = _maybe_inject_steer(message, pending_methods)
        if client_out_lock is None:
            write_framed_message(downstream, message)
        else:
            with client_out_lock:
                write_framed_message(downstream, message)


def refuse_conductor_descended_manage(
    message: dict[str, Any],
    *,
    dispatch_id: str,
    lookup: Any | None = None,
) -> dict[str, Any] | None:
    """Refuse state-changing ``manage`` before it is forwarded.

    *dispatch_id* is the GIW-stamped bridge env, not the tool argument. Omitting
    ``caller_dispatch_id`` does not skip this gate. An empty stamp (no
    cursor-sdk dispatch context) is forwarded.
    """
    stamp = (dispatch_id or "").strip()
    if not stamp or message.get("method") != "tools/call":
        return None
    params = message.get("params")
    if not isinstance(params, dict) or params.get("name") != "manage":
        return None
    arguments = params.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    action = str(arguments.get("action") or "")
    from implement_admission.conductor_descent import (
        READ_ONLY_MANAGE_ACTIONS,
        descends_from_conductor,
        ledger_lineage_lookup,
        refusal_body,
    )

    if action in READ_ONLY_MANAGE_ACTIONS:
        return None

    reader = lookup if lookup is not None else ledger_lineage_lookup
    try:
        descended = descends_from_conductor(stamp, reader)
    except Exception as exc:
        print(
            f"mcp-bridge: conductor descent lookup failed dispatch_id={stamp}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return None
    if not descended:
        return None
    body = refusal_body()
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {
            "code": -32602,
            "message": body["error"],
            "data": {"reason": body["reason"]},
        },
    }


def _copy_downstream(
    downstream: BinaryIO,
    upstream: BinaryIO,
    *,
    pending_methods: dict[Any, str],
    review_gate: bool = False,
    allow: frozenset[str] | None = None,
    seat_thread: str | None = None,
    client_out: BinaryIO | None = None,
    client_out_lock: threading.Lock | None = None,
    observer: GenerateObserver | None = None,
    conductor_dispatch_id: str = "",
    conductor_lookup: Any | None = None,
) -> None:
    while True:
        message = read_framed_message(upstream)
        if message is None:
            break
        descent_refusal = refuse_conductor_descended_manage(
            message,
            dispatch_id=conductor_dispatch_id,
            lookup=conductor_lookup,
        )
        if descent_refusal is not None:
            if client_out is None:
                raise RuntimeError("conductor manage refusal requires client_out")
            if client_out_lock is None:
                write_framed_message(client_out, descent_refusal)
            else:
                with client_out_lock:
                    write_framed_message(client_out, descent_refusal)
            continue
        if review_gate and allow is not None:
            refusal = refuse_filtered_tools_call(
                message, allow=allow, seat_thread=seat_thread
            )
            if refusal is not None:
                if client_out is None:
                    raise RuntimeError("review gate refusal requires client_out")
                if client_out_lock is None:
                    write_framed_message(client_out, refusal)
                else:
                    with client_out_lock:
                        write_framed_message(client_out, refusal)
                continue
        msg_id = message.get("id")
        method = message.get("method")
        if msg_id is not None and isinstance(method, str):
            pending_methods[msg_id] = method
        if observer is not None:
            observer.on_request(message)
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

    pending_methods: dict[Any, str] = {}
    upstream_err: list[BaseException] = []
    client_out_lock = threading.Lock()
    # Records the CDP generates this dispatch fires (friction 34156); the
    # bridge env carries both the dispatch id and the shared steer spool.
    observer = GenerateObserver.from_env(child_env)

    def _upstream_worker() -> None:
        try:
            _copy_upstream(
                proc.stdout,
                sys.stdout.buffer,
                allow=allow,
                pending_methods=pending_methods,
                client_out_lock=client_out_lock,
                observer=observer,
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
            review_gate=allow is not None,
            allow=allow,
            seat_thread=(child_env.get(ULG_DISPATCH_THREAD_ENV) or "").strip() or None,
            client_out=sys.stdout.buffer,
            client_out_lock=client_out_lock,
            observer=observer,
            conductor_dispatch_id=(
                child_env.get(CURSOR_SDK_DISPATCH_ID_ENV) or ""
            ).strip(),
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
