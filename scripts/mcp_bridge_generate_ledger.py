"""Bridge-side ledger of CDP generates a cursor-sdk dispatch fired.

The stdio MCP middlebox (``mcp_bridge_contract_filter.run_filtered_stdio_proxy``)
is the one process that sees both ``CURSOR_SDK_DISPATCH_ID`` and the
``team_dispatch`` result carrying the CDP ``execution_id``. Stargate never
learns the caller dispatch id (bus turns say ``from=dispatch``), so GIW cannot
tell at terminal which replies the dispatch is still owed — friction 34156.

``GenerateObserver`` watches ``tools/call`` request/result pairs on the relay
hot path and appends one JSONL row per *admitted, non-terminal* CDP generate
to ``<ULG_STEER_SPOOL_DIR>/<dispatch_id>.cdp-generates.jsonl`` (the spool GIW
already shares with the bridge). R1 also records when the agent *receives* a
qualifying reply via ``agent_bus`` / ``agent_bus_read`` wait, get, or fetch —
only a received reply is excluded from outstanding. Filesystem append only; it
never raises into the relay — a lost row degrades to today's behaviour.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.mcp_bridge_steer_inject import (
    CURSOR_SDK_DISPATCH_ID_ENV,
    ULG_STEER_SPOOL_DIR_ENV,
)

CDP_REPLY_FROM_AGENT = "web-anthropic"
CDP_SUBSTRATE = "web-anthropic-cdp"
_GENERATE_TOOLS: frozenset[str] = frozenset({"team_dispatch"})
_OVERFLOW_TOOL = "dispatch"
_RECEIVE_TOOLS: frozenset[str] = frozenset({"agent_bus", "agent_bus_read"})
_RECEIVE_OPS: frozenset[str] = frozenset({"wait", "get", "fetch"})
_LEDGER_SUFFIX = ".cdp-generates.jsonl"


def generate_ledger_path(spool_dir: Path | str, dispatch_id: str) -> Path:
    """Sibling of the steer spool file; one JSONL row per fired CDP generate."""
    safe = dispatch_id.replace("/", "_").replace(":", "_")
    return Path(spool_dir) / f"{safe}{_LEDGER_SUFFIX}"


def read_generate_records(
    dispatch_id: str, *, spool_dir: Path | str
) -> list[dict[str, Any]]:
    """Fire rows for *dispatch_id*, oldest first (received markers skipped)."""
    if not dispatch_id:
        return []
    path = generate_ledger_path(spool_dir, dispatch_id)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict) or not parsed.get("execution_id"):
            continue
        if str(parsed.get("kind") or "") == "received":
            continue
        rows.append(parsed)
    return rows


def read_received_execution_ids(
    dispatch_id: str, *, spool_dir: Path | str
) -> set[str]:
    """Execution ids marked received in the bridge ledger."""
    if not dispatch_id:
        return set()
    path = generate_ledger_path(spool_dir, dispatch_id)
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("kind") or "") != "received":
            continue
        execution_id = parsed.get("execution_id")
        if isinstance(execution_id, str) and execution_id:
            out.add(execution_id)
    return out


def _tool_call_target(params: Any) -> tuple[str, dict[str, Any]] | None:
    """Resolve (tool name, arguments) for a tools/call, unwrapping the overflow form."""
    if not isinstance(params, dict):
        return None
    name = str(params.get("name") or "")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    if name == _OVERFLOW_TOOL:
        inner = str(arguments.get("tool") or "")
        inner_args = arguments.get("arguments")
        if isinstance(inner_args, str):
            try:
                inner_args = json.loads(inner_args)
            except json.JSONDecodeError:
                inner_args = {}
        return inner, inner_args if isinstance(inner_args, dict) else {}
    return name, arguments


def _is_generate_call(params: Any) -> bool:
    target = _tool_call_target(params)
    if target is None:
        return False
    name, arguments = target
    if name not in _GENERATE_TOOLS:
        return False
    op = str(arguments.get("op") or "generate").lower()
    return op == "generate"


def _receive_call(params: Any) -> dict[str, Any] | None:
    """Return wait/get/fetch args when the call is on agent_bus*."""
    target = _tool_call_target(params)
    if target is None:
        return None
    name, arguments = target
    if name not in _RECEIVE_TOOLS:
        return None
    # Unified agent_bus surface: tool=<op>, arguments=<json|dict>.
    op = str(arguments.get("tool") or arguments.get("op") or "").lower()
    inner = arguments.get("arguments")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            inner = {}
    if not isinstance(inner, dict):
        inner = {}
    if op not in _RECEIVE_OPS:
        return None
    execution_id = inner.get("execution_id")
    thread = inner.get("thread") or inner.get("thread_id")
    from_agent = inner.get("from_agent")
    return {
        "op": op,
        "execution_id": execution_id if isinstance(execution_id, str) else None,
        "thread": (
            str(thread).replace("agent-bus:", "")
            if isinstance(thread, str) and thread
            else None
        ),
        "from_agent": from_agent if isinstance(from_agent, str) else None,
    }


def _result_payload(result: Any) -> dict[str, Any] | None:
    """The tool's returned dict: ``structuredContent`` first, else content[0].text JSON."""
    if not isinstance(result, dict) or result.get("isError") is True:
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    text = first.get("text") if isinstance(first, dict) else None
    if not isinstance(text, str):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def cdp_generate_record(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Project a team_dispatch result onto a ledger row when it is a live CDP admit."""
    execution_id = payload.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        return None
    if payload.get("terminal") is True:
        return None
    reply_from = str(payload.get("reply_from_agent") or "")
    substrate = str(payload.get("substrate") or "")
    if reply_from != CDP_REPLY_FROM_AGENT and substrate != CDP_SUBSTRATE:
        return None
    poll_args: Any = {}
    poll_hint = payload.get("poll_hint")
    if isinstance(poll_hint, dict) and isinstance(poll_hint.get("arguments"), dict):
        poll_args = poll_hint["arguments"]
    thread_id = payload.get("thread_id") or poll_args.get("thread")
    if not isinstance(thread_id, str) or not thread_id:
        return None
    try:
        after_turn = int(poll_args.get("after_turn", 0) or 0)
    except (TypeError, ValueError):
        after_turn = 0
    return {
        "execution_id": execution_id,
        "thread_id": str(thread_id).replace("agent-bus:", ""),
        "after_turn": after_turn,
        "from_agent": reply_from or CDP_REPLY_FROM_AGENT,
        "model": str(payload.get("resolved_model") or "") or None,
        "fired_at": datetime.now(UTC).isoformat(),
    }


def _execution_ids_in_text(text: str, known: set[str]) -> set[str]:
    return {eid for eid in known if eid and eid in text}


def _received_from_wait(
    payload: dict[str, Any],
    *,
    pending_execution_id: str | None,
    pending_thread: str | None,
    pending_from_agent: str | None,
    known: set[str],
    known_by_thread: dict[str, str],
) -> set[str]:
    """Map a completed wait onto known CDP generates.

    Named ``execution_id`` / producer id win when they match a known generate.
    The no-id fallback (exactly one open generate) counts only when the request
    named no execution_id, its thread equals that generate's thread_id, and
    ``from_agent`` is web-anthropic (friction 34156 F3).
    """
    if payload.get("complete") is not True:
        return set()
    if payload.get("qualifying_reply_turn") is None:
        return set()
    producer = payload.get("producer")
    producer_eid = None
    if isinstance(producer, dict):
        raw = producer.get("execution_id")
        if isinstance(raw, str):
            producer_eid = raw
    for candidate in (pending_execution_id, producer_eid):
        if candidate and candidate in known:
            return {candidate}
    # Fallback: only an unqualified wait on the generate's own web-anthropic lane.
    if pending_execution_id is not None:
        return set()
    if len(known) != 1:
        return set()
    only = next(iter(known))
    if pending_from_agent != CDP_REPLY_FROM_AGENT:
        return set()
    gen_thread = known_by_thread.get(only)
    if not pending_thread or not gen_thread or pending_thread != gen_thread:
        return set()
    return {only}


def _received_from_get_or_fetch(
    payload: dict[str, Any], *, known: set[str]
) -> set[str]:
    found: set[str] = set()
    # get → single turn; fetch → {turns: [...]} or a list-shaped content.
    turns: list[Any] = []
    if isinstance(payload.get("turns"), list):
        turns = payload["turns"]
    elif "turn_number" in payload or "body" in payload or "subject" in payload:
        turns = [payload]
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if str(turn.get("from") or "") != CDP_REPLY_FROM_AGENT:
            continue
        blob = f"{turn.get('subject') or ''}\n{turn.get('body') or ''}"
        found |= _execution_ids_in_text(blob, known)
    return found


class GenerateObserver:
    """Pairs tools/call requests with their results; appends CDP admits / receives."""

    def __init__(self, *, dispatch_id: str, spool_dir: Path | str) -> None:
        self.dispatch_id = dispatch_id.strip()
        self.spool_dir = Path(spool_dir)
        self._pending: dict[Any, dict[str, Any]] = {}

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> GenerateObserver:
        """Build from env; disabled when dispatch id or spool dir is unset (F9)."""
        source = env if env is not None else os.environ
        spool = (source.get(ULG_STEER_SPOOL_DIR_ENV) or "").strip()
        dispatch_id = source.get(CURSOR_SDK_DISPATCH_ID_ENV, "")
        if not spool:
            # Do not fall back to cwd — GIW never reads that path.
            return cls(dispatch_id="", spool_dir=".")
        return cls(dispatch_id=dispatch_id, spool_dir=spool)

    @property
    def enabled(self) -> bool:
        return bool(self.dispatch_id)

    def on_request(self, message: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            msg_id = message.get("id")
            if msg_id is None or message.get("method") != "tools/call":
                return
            params = message.get("params")
            if _is_generate_call(params):
                self._pending[msg_id] = {"kind": "generate"}
                return
            receive = _receive_call(params)
            if receive is not None:
                self._pending[msg_id] = {"kind": "receive", **receive}
        except Exception:  # noqa: BLE001 — never into the relay
            return

    def on_result(self, message: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            msg_id = message.get("id")
            if msg_id is None:
                return
            pending = self._pending.pop(msg_id, None)
            if pending is None:
                return
            payload = _result_payload(message.get("result"))
            if payload is None:
                return
            if pending.get("kind") == "generate":
                record = cdp_generate_record(payload)
                if record is not None:
                    self._append(record)
                return
            if pending.get("kind") == "receive":
                self._observe_receive(payload, pending=pending)
        except Exception:  # noqa: BLE001 — never into the relay
            return

    def _known_generates(self) -> tuple[set[str], dict[str, str]]:
        """Outstanding fire ids and execution_id → thread_id."""
        received = read_received_execution_ids(
            self.dispatch_id, spool_dir=self.spool_dir
        )
        known: set[str] = set()
        by_thread: dict[str, str] = {}
        for row in read_generate_records(
            self.dispatch_id, spool_dir=self.spool_dir
        ):
            eid = row.get("execution_id")
            if not isinstance(eid, str) or not eid or eid in received:
                continue
            known.add(eid)
            thread = row.get("thread_id")
            if isinstance(thread, str) and thread:
                by_thread[eid] = str(thread).replace("agent-bus:", "")
        return known, by_thread

    def _observe_receive(
        self, payload: dict[str, Any], *, pending: dict[str, Any]
    ) -> None:
        known, known_by_thread = self._known_generates()
        if not known:
            return
        op = str(pending.get("op") or "")
        if op == "wait":
            hits = _received_from_wait(
                payload,
                pending_execution_id=pending.get("execution_id"),
                pending_thread=pending.get("thread"),
                pending_from_agent=pending.get("from_agent"),
                known=known,
                known_by_thread=known_by_thread,
            )
        elif op in ("get", "fetch"):
            hits = _received_from_get_or_fetch(payload, known=known)
        else:
            hits = set()
        for execution_id in sorted(hits):
            self._append(
                {
                    "kind": "received",
                    "execution_id": execution_id,
                    "tool": op,
                    "received_at": datetime.now(UTC).isoformat(),
                }
            )

    def _append(self, record: dict[str, Any]) -> None:
        path = generate_ledger_path(self.spool_dir, self.dispatch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")


__all__ = [
    "CDP_REPLY_FROM_AGENT",
    "CDP_SUBSTRATE",
    "GenerateObserver",
    "cdp_generate_record",
    "generate_ledger_path",
    "read_generate_records",
    "read_received_execution_ids",
]
