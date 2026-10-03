"""Bridge-side CDP generate ledger (friction:34156, done item 1 attribution).

The stdio MCP middlebox is the only process that knows both the cursor-sdk
dispatch id and the ``execution_id`` of every CDP generate that dispatch fires.
It appends one JSONL row per admitted CDP generate to the shared steer spool so
GIW can tell, at terminal, which replies are still outstanding.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.mcp_bridge_contract_filter import (  # noqa: E402
    _copy_downstream,
    _copy_upstream,
    read_framed_message,
    write_framed_message,
)
from scripts.mcp_bridge_generate_ledger import (  # noqa: E402
    GenerateObserver,
    generate_ledger_path,
    read_generate_records,
)

_EXEC = "b08ecb6d-4c1e-4b8e-9d4e-000000000001"


def _cdp_admit_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "op": "generate",
        "status": "running",
        "execution_id": _EXEC,
        "thread_id": "14692",
        "thread": "agent-bus:14692",
        "to_agent": "web-anthropic",
        "reply_from_agent": "web-anthropic",
        "resolved_model": "cdp/opus-5.5",
        "substrate": "web-anthropic-cdp",
        "poll_hint": {
            "tool": "wait",
            "arguments": {
                "thread": "14692",
                "after_turn": 3,
                "wait_seconds": 0,
                "completion": "proof_reply_from",
                "from_agent": "web-anthropic",
                "execution_id": _EXEC,
            },
        },
        "terminal": False,
    }
    payload.update(overrides)
    return payload


def _request(msg_id: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _result(
    msg_id: int, payload: object, *, structured: bool = False
) -> dict[str, object]:
    result: dict[str, object] = {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "isError": False,
    }
    if structured:
        result["structuredContent"] = payload
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


@pytest.fixture
def observer(tmp_path: Path) -> GenerateObserver:
    return GenerateObserver(dispatch_id="bb28fae31960-dd251b78", spool_dir=tmp_path)


def test_records_cdp_generate_admit_from_team_dispatch(
    observer: GenerateObserver, tmp_path: Path
) -> None:
    observer.on_request(
        _request(7, "team_dispatch", {"op": "generate", "model": "cdp/opus-5.5"})
    )
    observer.on_result(_result(7, _cdp_admit_payload()))
    rows = read_generate_records("bb28fae31960-dd251b78", spool_dir=tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["execution_id"] == _EXEC
    assert row["thread_id"] == "14692"
    assert row["after_turn"] == 3
    assert row["from_agent"] == "web-anthropic"
    assert row["model"] == "cdp/opus-5.5"
    assert row["fired_at"]
    assert generate_ledger_path(tmp_path, "bb28fae31960-dd251b78").name == (
        "bb28fae31960-dd251b78.cdp-generates.jsonl"
    )


def test_records_overflow_dispatch_form_and_structured_content(
    observer: GenerateObserver, tmp_path: Path
) -> None:
    observer.on_request(
        _request(
            8,
            "dispatch",
            {"tool": "team_dispatch", "arguments": json.dumps({"op": "generate"})},
        )
    )
    observer.on_result(_result(8, _cdp_admit_payload(), structured=True))
    rows = read_generate_records("bb28fae31960-dd251b78", spool_dir=tmp_path)
    assert [r["execution_id"] for r in rows] == [_EXEC]


def test_ignores_non_cdp_results_errors_and_terminal_payloads(
    observer: GenerateObserver, tmp_path: Path
) -> None:
    # Nested cursor-sdk generate: a different continuation plane (nest park).
    observer.on_request(
        _request(1, "team_dispatch", {"op": "generate", "seat": "cursor-sdk"})
    )
    observer.on_result(
        _result(
            1,
            _cdp_admit_payload(
                reply_from_agent="cursor-sdk",
                substrate="cursor-sdk",
                to_agent="cursor-sdk",
            ),
        )
    )
    # Synchronous generate that already carries its reply.
    observer.on_request(_request(2, "team_dispatch", {"op": "generate"}))
    observer.on_result(
        _result(2, _cdp_admit_payload(terminal=True, status="completed"))
    )
    # Tool error envelope.
    observer.on_request(_request(3, "team_dispatch", {"op": "generate"}))
    err = _result(3, _cdp_admit_payload())
    err["result"]["isError"] = True  # type: ignore[index]
    observer.on_result(err)
    # Not a tools/call at all.
    observer.on_request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    observer.on_result({"jsonrpc": "2.0", "id": 4, "result": {"tools": []}})
    # A different tool.
    observer.on_request(_request(5, "cortex", {"tool": "search"}))
    observer.on_result(_result(5, _cdp_admit_payload()))
    # Result for an id never seen (request raced the proxy start).
    observer.on_result(_result(99, _cdp_admit_payload()))
    assert read_generate_records("bb28fae31960-dd251b78", spool_dir=tmp_path) == []
    assert not generate_ledger_path(tmp_path, "bb28fae31960-dd251b78").exists()


def test_never_raises_on_the_relay_hot_path(tmp_path: Path) -> None:
    unwritable = tmp_path / "missing" / "deeper"
    observer = GenerateObserver(dispatch_id="x", spool_dir=unwritable)
    observer.on_request(_request(1, "team_dispatch", {"op": "generate"}))
    bad = _result(1, "not-json-object")
    bad["result"]["content"] = [{"type": "text", "text": "{not json"}]  # type: ignore[index]
    observer.on_result(bad)
    observer.on_result({"jsonrpc": "2.0", "id": 1, "result": None})
    observer.on_result({"jsonrpc": "2.0", "id": None})
    # Directory did not exist: the observer creates it rather than dropping the row.
    observer.on_request(_request(2, "team_dispatch", {"op": "generate"}))
    observer.on_result(_result(2, _cdp_admit_payload()))
    assert [
        r["execution_id"] for r in read_generate_records("x", spool_dir=unwritable)
    ] == [_EXEC]
    # Disabled observer (no dispatch id in env) is a no-op.
    off = GenerateObserver(dispatch_id="", spool_dir=tmp_path)
    off.on_request(_request(3, "team_dispatch", {"op": "generate"}))
    off.on_result(_result(3, _cdp_admit_payload()))
    assert read_generate_records("", spool_dir=tmp_path) == []


def test_proxy_copy_loops_feed_the_observer(tmp_path: Path) -> None:
    observer = GenerateObserver(dispatch_id="d-proxy", spool_dir=tmp_path)
    pending: dict[object, str] = {}

    client_in = io.BytesIO()
    write_framed_message(client_in, _request(11, "team_dispatch", {"op": "generate"}))
    client_in.seek(0)
    to_child = io.BytesIO()
    _copy_downstream(to_child, client_in, pending_methods=pending, observer=observer)
    to_child.seek(0)
    assert read_framed_message(to_child)["id"] == 11
    assert pending == {11: "tools/call"}

    from_child = io.BytesIO()
    write_framed_message(from_child, _result(11, _cdp_admit_payload()))
    from_child.seek(0)
    to_client = io.BytesIO()
    _copy_upstream(
        from_child, to_client, allow=None, pending_methods=pending, observer=observer
    )
    to_client.seek(0)
    relayed = read_framed_message(to_client)
    assert relayed["id"] == 11  # the frame is relayed unchanged
    assert json.loads(relayed["result"]["content"][0]["text"])["execution_id"] == _EXEC
    assert [
        r["execution_id"] for r in read_generate_records("d-proxy", spool_dir=tmp_path)
    ] == [_EXEC]
