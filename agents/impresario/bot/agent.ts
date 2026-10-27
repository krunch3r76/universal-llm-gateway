import { defineAgent } from "@grok/agent-sdk";

export default defineAgent({
  name: "impresario-sdk",
  model: "composer-2.5",
  // AgentConfig.tools allowlist (@grok/agent-sdk types.d.ts ~L239): only Read
  // from harness builtins; server tools still ride the always-on "mcp" grant.
  tools: ["read"],
  concurrency: { maxRunningTurns: 2 },
  local: { sandbox: false },
});
