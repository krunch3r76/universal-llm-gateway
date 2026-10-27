import { defineAgent } from "@grok/agent-sdk";

export default defineAgent({
  name: "impresario-sdk",
  model: "composer-2.5",
  tools: ["read", "grep", "glob", "ls"],
  concurrency: { maxRunningTurns: 2 },
  local: { sandbox: false },
});
