import { defineAgent } from "@grok/agent-sdk";

export default defineAgent({
  name: "impresario-sdk",
  model: "composer-2.5",
  // Non-empty allowlist grants MCP so server tools (read_workspace, bus_send, …) reach the model.
  // Empty tools:[] would leave the harness with no MCP grant (SDK resolveHostHarnessToolOptions).
  tools: ["mcp"],
  concurrency: { maxRunningTurns: 2 },
  local: { sandbox: false },
});
