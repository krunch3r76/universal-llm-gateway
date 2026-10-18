"""CDP roster read and agent-bus reply for the context-hop observer.

Parse, thresholds, allowlist, cooldown, and deferral stay in
``grokbot_egress_watch_logic``. This module does not open sqlite.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from urllib.error import URLError
from pathlib import Path
from typing import Any

_ROW_KEYS = ("agent_id", "name", "lastMessageId", "is_running", "updated_at_ms")
DEFAULT_CDP_PORTS: tuple[int, ...] = (9340, 9339)
HOP_THREAD = "15851"
HOP_TO_AGENT = "grok-bot-gc1"
HOP_FROM_AGENT = "manage"

_CONTEXT_JS = """(() => {
  const root = document.getElementById("root") || document.body.firstElementChild;
  const fk = root ? Object.keys(root).find(k => k.startsWith("__reactContainer") || k.startsWith("__reactFiber")) : null;
  if (!fk) return [];
  const stack = [root[fk]];
  const seen = new Set();
  let client = null;
  while (stack.length) {
    const node = stack.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    if (node.memoizedProps?.value?.roster) { client = node.memoizedProps.value; break; }
    if (node.child) stack.push(node.child);
    if (node.sibling) stack.push(node.sibling);
  }
  if (!client) return [];
  const rows = client.roster?.snapshots?.get
    ? (client.roster.snapshots.get()?.agents?.rows || [])
    : [];
  return rows.map(r => ({
    agent_id: r.id,
    name: r.name,
    lastMessageId: r.lastMessageId,
    is_running: Boolean(r.isRunning),
    updated_at_ms: (typeof r.updatedAt === "number" ? r.updatedAt : (typeof r.lastActivityAt === "number" ? r.lastActivityAt : (typeof r.updatedAtMs === "number" ? r.updatedAtMs : null))),
  }));
})()"""


def _agent_bus_script() -> Path:
    return Path(__file__).resolve().parents[4] / "scripts" / "agent-bus"


def _normalize_rows(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = {key: item.get(key) for key in _ROW_KEYS}
        rows.append(row)
    return rows


def _merge_rows_by_agent_id(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_agent: dict[str, dict[str, object]] = {}
    for row in rows:
        aid = row.get("agent_id")
        if not isinstance(aid, str):
            continue
        existing = by_agent.get(aid)
        if existing is None:
            by_agent[aid] = row
            continue
        new_ts = row.get("updated_at_ms")
        old_ts = existing.get("updated_at_ms")
        if isinstance(new_ts, int) and (
            not isinstance(old_ts, int) or new_ts > old_ts
        ):
            by_agent[aid] = row
    return list(by_agent.values())


async def probe_grok_agent_context(
    *,
    evaluate: Callable[[str], Awaitable[Any]] | None = None,
    cdp_port: int | None = None,
    cdp_ports: Sequence[int] = DEFAULT_CDP_PORTS,
) -> list[dict[str, object]]:
    """Return roster rows from a read-only CDP expression. No model call."""
    if evaluate is not None:
        raw = await evaluate(_CONTEXT_JS)
        return _normalize_rows(raw)

    from cursor_bridge.grok_computer import _cdp_eval, _resolve_ws_url

    ports = (cdp_port,) if cdp_port is not None else tuple(cdp_ports)
    merged: list[dict[str, object]] = []
    for port in ports:
        try:
            ws_url = _resolve_ws_url(port, timeout_s=5.0)
            raw = await _cdp_eval(ws_url, _CONTEXT_JS, await_promise=False)
        except (URLError, OSError, ConnectionError, TimeoutError):
            continue
        merged.extend(_normalize_rows(raw))
    return _merge_rows_by_agent_id(merged)


def _run_reply(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True)


def hop_reply_argv(*, subject: str, body: str, after_turn: int) -> list[str]:
    """Argv for ``scripts/agent-bus reply``. ``after_turn`` is the caller's integer."""
    return [
        str(_agent_bus_script()),
        "reply",
        "--thread",
        HOP_THREAD,
        "--to",
        HOP_TO_AGENT,
        "--from-agent",
        HOP_FROM_AGENT,
        "--subject",
        subject,
        "--body",
        body,
        "--after-turn",
        str(after_turn),
    ]


async def reply_context_hop(
    *,
    subject: str,
    body: str,
    after_turn: int,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Post one notice via ``asyncio.to_thread``. Does not read the bus store."""
    argv = hop_reply_argv(subject=subject, body=body, after_turn=after_turn)
    run = runner or _run_reply
    completed = await asyncio.to_thread(run, argv)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"agent-bus reply exit {completed.returncode}")
    return completed
