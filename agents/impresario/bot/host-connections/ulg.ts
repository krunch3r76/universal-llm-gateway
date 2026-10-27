import { defineConnection } from "@grok/agent-sdk/connections";

/** House ulg-code MCP endpoint (same as Cursor `vortex-code` in ~/.cursor/mcp.json). Path: /mcp/code */
export const ULG_MCP_HTTP_URL = "https://mcp.k-1.me/mcp/code";

export default defineConnection({
  type: "http",
  url: ULG_MCP_HTTP_URL,
  headers: {
    authorization: "Bearer ${ULG_MCP_TOKEN}",
  },
  tools: ["agent_bus", "agent_bus_read", "fs", "team_dispatch", "cortex"],
});
