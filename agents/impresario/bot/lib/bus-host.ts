import type { HostContext } from "@grok/agent-sdk";

import { callHostMcpTool } from "./bus-mcp-retry.js";
import { fixtureBusOp, fixturesEnabled } from "./bus-fixtures.js";

export async function hostBusCall(
  host: HostContext,
  tool: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureBusOp(tool, payload);
  }
  const mcpTool = tool === "get" ? "agent_bus_read" : "agent_bus";
  // Streamable HTTP MCP rejects a top-level `from_agent` kwarg; author via `from` in JSON when needed.
  return callHostMcpTool(() =>
    host.mcp.callTool("ulg", mcpTool, {
      tool,
      arguments: JSON.stringify(payload),
    }),
  );
}

export async function hostBusSend(
  host: HostContext,
  payload: Record<string, unknown>,
): Promise<unknown> {
  return hostBusCall(host, "send", payload);
}
