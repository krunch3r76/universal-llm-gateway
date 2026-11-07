"""Execute generated roster JS against a fake React fiber in Node."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from cursor_bridge.grok_roster_js import (
    js_ensure_hidden,
    js_list,
    js_retire,
    js_select,
    js_spawn,
)

pytestmark = pytest.mark.offline

_BASE_HARNESS = r"""
const captured = {};
let rows = [
  {id: "vis", name: "Visible", isHiddenFromSidebar: false},
  {id: "hid", name: "Hidden", isHiddenFromSidebar: true},
  {id: "ret", name: "Old (retired)", isHiddenFromSidebar: false},
];
const selection = {
  current: "vis",
  async select(id) { captured.selectId = id; selection.current = id; },
  snapshots: { get() { return { currentAgentId: selection.current }; } },
};
const client = {
  roster: {
    snapshots: { get() { return { agents: { rows } }; } },
    async createAgent(payload) {
      captured.payload = payload;
      return captured.createResult === undefined ? { id: "new-1" } : captured.createResult;
    },
    async updateAgent(id, patch) {
      const row = rows.find(r => r.id === id);
      if (row) row.name = patch.name;
    },
    async setAgentHiddenFromSidebar(id, hidden) {
      if (captured.skipHideFlip && id === "vis") return;
      const row = rows.find(r => r.id === id);
      if (row) row.isHiddenFromSidebar = hidden;
    },
  },
  selection,
};
const root = { __reactContainer$x: { memoizedProps: { value: client } } };
global.document = { getElementById() { return root; }, body: { firstElementChild: root } };
const script = JSON.parse(process.argv[1]);
if (script.createResult !== undefined) captured.createResult = script.createResult;
if (script.skipHideFlip) captured.skipHideFlip = true;
(async () => {
  const out = {};
  for (const [key, expr] of Object.entries(script.exprs)) {
    out[key] = await eval(expr);
  }
  out.captured = captured;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run_node(script: dict[str, object]) -> dict[str, object]:
    node = shutil.which("node")
    assert node, "node is required to execute the roster fixture"
    proc = subprocess.run(
        [node, "-e", _BASE_HARNESS, json.dumps(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_node_executes_roster_filters_create_and_retire() -> None:
    out = _run_node(
        {
            "exprs": {
                "listed": js_list(include_hidden=False, include_retired=False),
                "spawn": js_spawn(client_nonce="nonce-1", name="Bot", description="", hidden=True),
                "retire": js_retire("vis"),
                "again": js_retire("vis"),
            }
        }
    )
    names = [row["name"] for row in out["listed"]["agents"]]
    assert names == ["Visible"]
    assert out["captured"]["payload"] == {
        "clientNonce": "nonce-1",
        "name": "Bot",
        "description": "",
        "creationRoute": {"kind": "box"},
        "isKickstartRequested": False,
    }
    assert out["spawn"]["agent_id"] == "new-1"
    assert out["retire"]["ok"] is True
    assert out["retire"]["name"].endswith(" (retired)")
    assert out["again"]["name"] == out["retire"]["name"]
    assert out["again"]["name"].count("(retired)") == 1


def test_node_spawn_create_agent_no_id() -> None:
    out = _run_node(
        {
            "createResult": {},
            "exprs": {
                "spawn": js_spawn(client_nonce="nonce-2", name="Bot", description="", hidden=True),
            },
        }
    )
    assert out["spawn"]["ok"] is False
    assert out["spawn"]["code"] == "create_agent_no_id"
    assert out["spawn"]["created"] is False


def test_node_js_select_calls_selection_select() -> None:
    out = _run_node({"exprs": {"picked": js_select("hid")}})
    assert out["captured"]["selectId"] == "hid"
    assert out["picked"]["ok"] is True
    assert out["picked"]["currentAgentId"] == "hid"


def test_node_js_ensure_hidden_readback_and_missing_row() -> None:
    out = _run_node(
        {
            "skipHideFlip": True,
            "exprs": {
                "hide_fail": js_ensure_hidden("vis"),
                "missing": js_ensure_hidden("ghost"),
            },
        }
    )
    assert out["hide_fail"]["ok"] is False
    assert out["hide_fail"]["code"] == "hide_unverified"
    assert out["hide_fail"]["hidden"] is False
    assert out["missing"]["ok"] is False
    assert out["missing"]["code"] == "hide_unverified"


def test_node_js_retire_row_not_found() -> None:
    out = _run_node({"exprs": {"gone": js_retire("ghost")}})
    assert out["gone"]["ok"] is False
    assert out["gone"]["code"] == "row_not_found"
