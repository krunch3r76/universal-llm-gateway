"""Execute generated roster JS against a fake React fiber in Node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from cursor_bridge.grok_roster_js import (
    js_ensure_hidden,
    js_list,
    js_retire,
    js_select,
    js_spawn,
)


def _node_bin() -> str:
    for candidate in (
        shutil.which("node"),
        "/home/io/.nvm/versions/node/v24.14.0/bin/node",
        "/usr/bin/node",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    nvm = Path("/home/io/.nvm/versions/node")
    if nvm.is_dir():
        found = sorted(nvm.glob("*/bin/node"), reverse=True)
        if found:
            return str(found[0])
    return ""


pytestmark = [
    pytest.mark.offline,
    pytest.mark.skipif(not _node_bin(), reason="node is required to execute roster JS"),
]

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
      if (captured.latePersist) {
        if (captured.lateDuplicate) {
          rows.push({id: "late-a", name: payload.name, isHiddenFromSidebar: false});
          rows.push({id: "late-b", name: payload.name, isHiddenFromSidebar: false});
        } else if (captured.leftoverNew) {
          rows.push({id: "stray-new", name: "Unrelated", isHiddenFromSidebar: false});
        } else {
          rows.push({id: "late-1", name: payload.name, isHiddenFromSidebar: false});
        }
        return captured.createResult === undefined ? {} : captured.createResult;
      }
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
if (script.latePersist) captured.latePersist = true;
if (script.lateDuplicate) captured.lateDuplicate = true;
if (script.leftoverNew) captured.leftoverNew = true;
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
    node = _node_bin()
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


@pytest.mark.parametrize(
    "create_result,expected_id",
    [
        ("str-9", "str-9"),
        ({"agentId": "aid-1"}, "aid-1"),
        ({"id": "id-1"}, "id-1"),
        ({"header": {"id": "hdr-1"}}, "hdr-1"),
        ({"_inner": {"agentId": "in-1"}}, "in-1"),
        ({"composerId": "cmp-1"}, "cmp-1"),
        ({"stableId": "st-1"}, "st-1"),
        ({"agent": {"id": "agent-wrap-1"}}, "agent-wrap-1"),
        ({"data": {"id": "data-wrap-1"}}, "data-wrap-1"),
    ],
    ids=[
        "string",
        "agentId",
        "id",
        "header",
        "inner",
        "composerId",
        "stableId",
        "agent.id",
        "data.id",
    ],
)
def test_node_spawn_id_extract_shapes(create_result: object, expected_id: str) -> None:
    out = _run_node(
        {
            "createResult": create_result,
            "exprs": {
                "spawn": js_spawn(client_nonce="n", name="Bot", description="", hidden=True),
            },
        }
    )
    assert out["spawn"]["ok"] is True
    assert out["spawn"]["agent_id"] == expected_id


def test_node_spawn_late_roster_diff_finds_id() -> None:
    out = _run_node(
        {
            "latePersist": True,
            "createResult": {},
            "exprs": {
                "spawn": js_spawn(client_nonce="late", name="Bot", description="", hidden=True),
            },
        }
    )
    assert out["spawn"]["ok"] is True
    assert out["spawn"]["agent_id"] == "late-1"
    assert out["spawn"]["recovered"] == "roster_diff"


def test_node_spawn_leftover_new_rows_ambiguous() -> None:
    out = _run_node(
        {
            "latePersist": True,
            "leftoverNew": True,
            "createResult": {},
            "exprs": {
                "spawn": js_spawn(client_nonce="left", name="Bot", description="", hidden=True),
            },
        }
    )
    assert out["spawn"]["ok"] is False
    assert out["spawn"]["code"] == "create_agent_ambiguous"
    assert out["spawn"]["needs_operator"] is True


def test_node_spawn_late_duplicate_names_ambiguous() -> None:
    out = _run_node(
        {
            "latePersist": True,
            "lateDuplicate": True,
            "createResult": {},
            "exprs": {
                "spawn": js_spawn(client_nonce="dup", name="Bot", description="", hidden=True),
            },
        }
    )
    assert out["spawn"]["ok"] is False
    assert out["spawn"]["code"] == "create_agent_ambiguous"
    assert out["spawn"]["needs_operator"] is True


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
