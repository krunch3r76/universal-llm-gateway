import { defineAgent } from "@grok/agent-sdk";

export default defineAgent({
  name: "impresario-sdk",
  model: "composer-2.5",
  // Read-only harness tool for turn.json / score.md when workspaceFiles are insufficient.
  tools: ["read"],
  concurrency: { maxRunningTurns: 2 },
  local: { sandbox: false },
});
