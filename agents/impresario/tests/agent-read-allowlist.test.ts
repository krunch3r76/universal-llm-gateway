import assert from "node:assert/strict";
import { describe, it } from "node:test";

import agentConfig from "../bot/agent.js";

describe("impresario harness tools (R2)", () => {
  it("does not expose harness Read; workspace reads use read_workspace server tool", () => {
    assert.deepEqual(agentConfig.tools, []);
  });
});
