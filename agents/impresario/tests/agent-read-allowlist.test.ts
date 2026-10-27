import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { resolveHostHarnessToolOptions } from "../node_modules/@grok/agent-sdk/dist/filesystem.js";

import agentConfig from "../bot/agent.js";

describe("impresario harness tools (stage 11 S1)", () => {
  it("agent tools allowlist is mcp-only and resolves without harness Read", () => {
    assert.deepEqual(agentConfig.tools, ["mcp"]);
    const resolved = resolveHostHarnessToolOptions(agentConfig.tools);
    assert.deepEqual(resolved, { tools: ["mcp"] });
    assert.equal(resolved.tools?.includes("read"), false);
    assert.equal(resolved.tools?.includes("Read"), false);
  });
});
