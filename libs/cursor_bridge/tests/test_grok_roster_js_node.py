"""Execute generated roster JS against a fake React fiber in Node."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from cursor_bridge.grok_roster_js import js_list, js_retire, js_spawn

pytestmark = pytest.mark.offline

_HARNESS = r"""
const captured = {};
const rows = [
  {id: "vis", name: "Visible", isHiddenFromSidebar: false},
  {id: "hid", name: "Hidden", isHiddenFromSidebar: true},
  {id: "ret", name: "Old (retired)", isHiddenFromSidebar: false},
];
const client = {
  roster: {
    snapshots: { get() { return { agents: { rows } }; } },
    async createAgent(payload) { captured.payload = payload; return { id: "new-1" }; },
    async updateAgent(id, patch) {
      const row = rows.find(r => r.id === id);
      if (row) row.name = patch.name;
    },
    async setAgentHiddenFromSidebar(id, hidden) {
      const row = rows.find(r => r.id === id);
      if (row) row.isHiddenFromSidebar = hidden;
    },
  },
  selection: { snapshots: { get() { return { currentAgentId: "vis" }; } } },
};
const root = { __reactContainer$x: { memoizedProps: { value: client } } };
global.document = { getElementById() { return root; }, body: { firstElementChild: root } };
const exprs = JSON.parse(process.argv[1]);
(async () => {
  const listed = await eval(exprs.list);
  const spawned = await eval(exprs.spawn);
  const retired = await eval(exprs.retire);
  const again = await eval(exprs.retire);
  process.stdout.write(JSON.stringify({ listed, spawned, captured, retired, again }));
})();
"""


def test_node_executes_roster_filters_create_and_retire() -> None:
    node = shutil.which("node")
    assert node, "node is required to execute the roster fixture"
    exprs = {
        "list": js_list(include_hidden=False, include_retired=False),
        "spawn": js_spawn(client_nonce="nonce-1", name="Bot", description="", hidden=True),
        "retire": js_retire("vis"),
    }
    proc = subprocess.run(
        [node, "-e", _HARNESS, json.dumps(exprs)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    names = [row["name"] for row in out["listed"]["agents"]]
    assert names == ["Visible"]
    assert out["captured"]["payload"] == {
        "clientNonce": "nonce-1",
        "name": "Bot",
        "description": "",
        "creationRoute": {"kind": "box"},
        "isKickstartRequested": False,
    }
    assert out["spawned"]["agent_id"] == "new-1"
    assert out["retired"]["ok"] is True
    assert out["retired"]["name"].endswith(" (retired)")
    assert out["again"]["name"] == out["retired"]["name"]
    assert out["again"]["name"].count("(retired)") == 1
