"""CDP roster read and agent-bus reply for the context-hop observer.

Parse, thresholds, allowlist, cooldown, and deferral stay in
``grokbot_egress_watch_logic``. This module does not open sqlite.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_ROW_KEYS = ("agent_id", "name", "lastMessageId", "is_running", "updated_at_ms")
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
    updated_at_ms: r.updatedAtMs,
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


async def probe_grok_agent_context(
    *,
    evaluate: Callable[[str], Awaitable[Any]] | None = None,
    cdp_port: int = 9339,
) -> list[dict[str, object]]:
    """Return roster rows from a read-only CDP expression. No model call."""
    if evaluate is None:
        from cursor_bridge.grok_computer import _cdp_eval, _resolve_ws_url

        ws_url = _resolve_ws_url(cdp_port, timeout_s=5.0)
        raw = await _cdp_eval(ws_url, _CONTEXT_JS, await_promise=False)
    else:
        raw = await evaluate(_CONTEXT_JS)
    return _normalize_rows(raw)


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
