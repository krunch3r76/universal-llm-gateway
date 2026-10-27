import assert from "node:assert/strict";
import { describe, it } from "node:test";

import agentConfig from "../bot/agent.js";

describe("impresario harness Read allowlist (stage 9 N1)", () => {
  it("agent tools allowlist is exactly Read for workspace preload files", () => {
    assert.deepEqual(agentConfig.tools, ["read"]);
  });
});
