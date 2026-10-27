import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConnection } from "@grok/agent-sdk/connections";

const repoRoot = path.resolve(fileURLToPath(import.meta.url), "../../../..");

export default defineConnection({
  command: "python3",
  args: [path.join(repoRoot, "scripts", "mcp-fastmcp-remote-bridge.py")],
  cwd: repoRoot,
  env: {
    MCP_TOKEN: "${ULG_MCP_TOKEN}",
  },
  tools: ["agent_bus", "agent_bus_read", "fs", "team_dispatch", "cortex"],
});
