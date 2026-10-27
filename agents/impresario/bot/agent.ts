import { defineAgent } from "@grok/agent-sdk";

export default defineAgent({
  name: "impresario-sdk",
  model: "composer-2.5",
  // Workspace file reads: authored read_workspace only (no harness Read — cannot leave workspaceDir).
  tools: [],
  concurrency: { maxRunningTurns: 2 },
  local: { sandbox: false },
});
