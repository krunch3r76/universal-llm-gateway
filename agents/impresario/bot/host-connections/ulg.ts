import { defineConnection } from "@grok/agent-sdk/connections";

import {
  logUlgMcpStartupResolvedHost,
  resolveUlgMcpHttpUrl,
} from "../lib/ulg-mcp-url.js";

/** Resolved at process start from ULG_MCP_HTTP_URL or loopback default. */
export const ULG_MCP_HTTP_URL = resolveUlgMcpHttpUrl();

logUlgMcpStartupResolvedHost(ULG_MCP_HTTP_URL);

export default defineConnection({
  type: "http",
  url: ULG_MCP_HTTP_URL,
  headers: {
    authorization: "Bearer ${ULG_MCP_TOKEN}",
  },
  tools: ["agent_bus", "agent_bus_read", "fs", "team_dispatch", "cortex"],
});
