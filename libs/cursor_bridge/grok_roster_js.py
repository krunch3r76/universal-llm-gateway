"""Grok renderer expressions for roster list, select, create, hide, and retire.

The page script walks the React fiber for ``client.roster``. Callers evaluate
the returned source over CDP. This module does not open a socket.
"""

from __future__ import annotations

import json

RETIRED_SUFFIX = " (retired)"

FIND_CLIENT = """
  const root = document.getElementById("root") || document.body.firstElementChild;
  const fk = root ? Object.keys(root).find(k => k.startsWith("__reactContainer") || k.startsWith("__reactFiber")) : null;
  if (!fk) return null;
  const stack = [root[fk]];
  const seen = new Set();
  while (stack.length) {
    const node = stack.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    if (node.memoizedProps?.value?.roster) return node.memoizedProps.value;
    if (node.child) stack.push(node.child);
    if (node.sibling) stack.push(node.sibling);
  }
  return null;
"""


def _client() -> str:
    return f"(() => {{ {FIND_CLIENT} }})()"


def js_list(*, include_hidden: bool, include_retired: bool) -> str:
    """Roster rows plus ``currentAgentId``. Defaults keep hidden and drop retired."""
    ih = "true" if include_hidden else "false"
    ir = "true" if include_retired else "false"
    return f"""(async () => {{
  const client = {_client()};
  if (!client) return {{ok: false, code: "grok_client_missing"}};
  const rows = client.roster?.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
  const includeHidden = {ih};
  const includeRetired = {ir};
  const agents = [];
  for (const r of rows) {{
    const name = String(r.name || "");
    const hidden = Boolean(r.isHiddenFromSidebar || r.hiddenFromSidebar);
    if (!includeHidden && hidden) continue;
    if (!includeRetired && name.endsWith({json.dumps(RETIRED_SUFFIX)})) continue;
    agents.push({{id: r.id, name, isHiddenFromSidebar: hidden, isRunning: Boolean(r.isRunning)}});
  }}
  const sel = client.selection?.snapshots?.get ? client.selection.snapshots.get() : null;
  return {{ok: true, agents, currentAgentId: sel?.currentAgentId ?? null, transport: "cdp_grok_roster"}};
}})()"""


def js_select(agent_id: str) -> str:
    """Select ``agent_id`` and return whether ``currentAgentId`` matches."""
    aid = json.dumps(agent_id)
    return f"""(async () => {{
  const client = {_client()};
  if (!client?.selection?.select) return {{ok: false, code: "selection_api_missing"}};
  await client.selection.select({aid});
  const sel = client.selection.snapshots?.get ? client.selection.snapshots.get() : null;
  const current = sel?.currentAgentId ?? null;
  return {{ok: current === {aid}, currentAgentId: current, expectedAgentId: {aid}}};
}})()"""


def js_spawn(*, client_nonce: str, name: str, description: str, hidden: bool) -> str:
    """``createAgent`` with the box route. Hide is a separate ensure step."""
    payload = {
        "clientNonce": client_nonce,
        "name": name,
        "description": description,
        "creationRoute": {"kind": "box"},
        "isKickstartRequested": False,
    }
    nm = json.dumps(name)
    return f"""(async () => {{
  const client = {_client()};
  if (!client?.roster?.createAgent) return {{ok: false, code: "roster_api_missing", created: false}};
  const rosterRows = () => {{
    const rows = client.roster?.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
    return rows.map((r) => ({{id: String(r.id || ''), name: String(r.name || '')}}));
  }};
  const beforeIds = new Set(rosterRows().map((r) => r.id).filter(Boolean));
  const extractAgentId = (created) => {{
    if (typeof created === 'string') {{
      const s = created.trim();
      return s || null;
    }}
    if (!created || typeof created !== 'object') return null;
    const h = created.header;
    const inner = created._inner;
    const fromAgent = created.agent && created.agent.id;
    const fromData = created.data && created.data.id;
    return (h && h.id) || (inner && inner.agentId) || created.agentId
      || created.composerId || created.id || created.stableId || fromAgent || fromData || null;
  }};
  const createShape = (created) => {{
    if (created === null || created === undefined) return {{kind: 'null'}};
    if (typeof created === 'string') return {{kind: 'string', len: created.length}};
    if (typeof created === 'object') return {{kind: 'object', keys: Object.keys(created).slice(0, 12)}};
    return {{kind: typeof created}};
  }};
  const created = await client.roster.createAgent({json.dumps(payload)});
  let agentId = extractAgentId(created);
  let recovered = agentId ? 'return_value' : null;
  const idleCap = 8;
  if (!agentId) {{
    let prev = JSON.stringify(rosterRows());
    for (let i = 0; i < idleCap; i++) {{
      await Promise.resolve();
      const after = rosterRows();
      const newRows = after.filter((r) => r.id && !beforeIds.has(r.id));
      const byName = newRows.filter((r) => r.name === {nm});
      if (byName.length === 1) {{
        agentId = byName[0].id;
        recovered = 'roster_diff';
        break;
      }}
      if (byName.length > 1) {{
        return {{ok: false, code: 'create_agent_ambiguous', needs_operator: true, created: true,
          create_shape: createShape(created), clientNonce: {json.dumps(client_nonce)}}};
      }}
      const snap = JSON.stringify(after);
      if (snap === prev) break;
      prev = snap;
    }}
  }}
  if (!agentId) {{
    const added = rosterRows().filter((r) => r.id && !beforeIds.has(r.id));
    if (added.length > 0) {{
      return {{ok: false, code: 'create_agent_ambiguous', needs_operator: true, created: true,
        create_shape: createShape(created), clientNonce: {json.dumps(client_nonce)}}};
    }}
    return {{ok: false, code: 'create_agent_no_id', created: false, create_shape: createShape(created)}};
  }}
  return {{ok: true, created: true, agent_id: agentId, recovered, create_shape: createShape(created),
    clientNonce: {json.dumps(client_nonce)}, hidden_requested: {json.dumps(bool(hidden))}}};
}})()"""


def js_confirm_spawn_sidebar_active(agent_id: str) -> str:
    """Read-only: sidebar row + selection agree on ``agent_id`` for visible spawn confirm."""
    aid = json.dumps(agent_id)
    return f"""(async () => {{
  const client = {_client()};
  const sel = client?.selection?.snapshots?.get ? client.selection.snapshots.get() : null;
  const current = sel?.currentAgentId ?? null;
  const row = document.querySelector('button.sand-agent-item[data-agent-id=' + {aid} + ']');
  const rowId = row?.getAttribute?.('data-agent-id')?.trim() ?? '';
  const ariaCurrent = row?.getAttribute?.('aria-current') === 'page';
  const dataActive = row?.getAttribute?.('data-active') === 'true';
  const rowMarksActive = Boolean(row && rowId === {aid} && (ariaCurrent || dataActive));
  const selectionMatch = current === {aid};
  const ok = Boolean(rowMarksActive && selectionMatch && rowId === {aid});
  return {{
    ok,
    currentAgentId: current,
    row_agent_id: rowId || null,
    aria_current: ariaCurrent,
    data_active: dataActive,
    expected: {aid},
  }};
}})()"""


def js_ensure_hidden(agent_id: str) -> str:
    """Hide ``agent_id`` and report the roster readback, not the request flag."""
    aid = json.dumps(agent_id)
    return f"""(async () => {{
  const client = {_client()};
  if (!client?.roster?.setAgentHiddenFromSidebar) return {{ok: false, code: "hide_api_missing", agent_id: {aid}}};
  await client.roster.setAgentHiddenFromSidebar({aid}, true);
  const rows = client.roster.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
  const row = rows.find(r => r.id === {aid});
  const hidden = Boolean(row && (row.isHiddenFromSidebar || row.hiddenFromSidebar));
  return {{ok: hidden, code: hidden ? "hidden" : "hide_unverified", agent_id: {aid}, hidden}};
}})()"""


def js_retire(agent_id: str) -> str:
    """Rename with a single retired suffix, hide, and read both back."""
    aid = json.dumps(agent_id)
    suffix = json.dumps(RETIRED_SUFFIX)
    return f"""(async () => {{
  const client = {_client()};
  if (!client?.roster) return {{ok: false, code: "roster_api_missing"}};
  const rows = client.roster.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
  const row = rows.find(r => r.id === {aid});
  if (!row) return {{ok: false, code: "row_not_found", agent_id: {aid}}};
  if (typeof client.roster.updateAgent !== "function" || typeof client.roster.setAgentHiddenFromSidebar !== "function") {{
    return {{ok: false, code: "renderer_api_missing", agent_id: {aid}}};
  }}
  const baseName = String(row.name || "");
  const nextName = baseName.endsWith({suffix}) ? baseName : baseName + {suffix};
  await client.roster.updateAgent({aid}, {{name: nextName}}, ["name"]);
  await client.roster.setAgentHiddenFromSidebar({aid}, true);
  const after = client.roster.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
  const updated = after.find(r => r.id === {aid});
  const hidden = Boolean(updated && (updated.isHiddenFromSidebar || updated.hiddenFromSidebar));
  const renamed = Boolean(updated && String(updated.name || "") === nextName);
  return {{ok: renamed && hidden, code: renamed && hidden ? "retired" : "retire_unverified", agent_id: {aid}, name: updated ? updated.name : nextName, hidden, renamed}};
}})()"""


__all__ = [
    "FIND_CLIENT",
    "RETIRED_SUFFIX",
    "js_confirm_spawn_sidebar_active",
    "js_ensure_hidden",
    "js_list",
    "js_retire",
    "js_select",
    "js_spawn",
]
