import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConnection } from "@grok/agent-sdk/connections";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../",
);

export default defineConnection({
  command: path.join(os.homedir(), ".venvs/universal/bin/python3"),
  args: [path.join(repoRoot, "scripts", "mcp-fastmcp-remote-bridge.py")],
  cwd: repoRoot,
  tools: ["agent_bus", "agent_bus_read", "fs", "team_dispatch", "cortex"],
});
