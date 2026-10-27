import { defineAgent } from "@grok/agent-sdk";

export default defineAgent({
  name: "impresario-sdk",
  model: "composer-2.5",
  // No harness builtins — workspace reads use server tool read_workspace.
  tools: [],
  concurrency: { maxRunningTurns: 2 },
  local: { sandbox: false },
});
