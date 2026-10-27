import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  checkE4CloseoutRun,
  type E4ToolCall,
} from "../evals/lib/e4-rule-check.js";

const WORK_KEY = "todo:reaper-amendment";

describe("E4 rule check (K109)", () => {
  it("pass fixture: required writes, log ignored, reads vary", () => {
    const calls: E4ToolCall[] = [
      { toolName: "Read", args: { path: "turn.json" } },
      { toolName: "score_read", args: {} },
      { toolName: "closeout_read", args: { dispatch_id: "d1" } },
      {
        toolName: "bus_send",
        args: {
          subject: `REQUEST|review|${WORK_KEY}`,
          to: "grok-bot-gc1",
        },
      },
      {
        toolName: "score_update",
        args: { section: "state", work_key: WORK_KEY, body: "review_requested" },
      },
      {
        toolName: "score_update",
        args: { section: "log", work_key: WORK_KEY, body: "optional log" },
      },
    ];
    assert.deepEqual(checkE4CloseoutRun(calls, WORK_KEY), { ok: true });
  });

  it("missing-required fixture: no bus_send REQUEST", () => {
    const calls: E4ToolCall[] = [
      { toolName: "closeout_read", args: { dispatch_id: "d1" } },
      {
        toolName: "score_update",
        args: { section: "state", work_key: WORK_KEY, body: "x" },
      },
    ];
    const result = checkE4CloseoutRun(calls, WORK_KEY);
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.match(result.reason, /bus_send/);
    }
  });

  it("forbidden-present fixture: commission called", () => {
    const calls: E4ToolCall[] = [
      { toolName: "closeout_read", args: { dispatch_id: "d1" } },
      {
        toolName: "bus_send",
        args: { subject: `REQUEST|review|${WORK_KEY}`, to: "grok-bot-gc1" },
      },
      {
        toolName: "score_update",
        args: { section: "state", work_key: WORK_KEY, body: "x" },
      },
      { toolName: "commission", args: { work_key: WORK_KEY } },
    ];
    const result = checkE4CloseoutRun(calls, WORK_KEY);
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.match(result.reason, /commission/);
    }
  });
});
