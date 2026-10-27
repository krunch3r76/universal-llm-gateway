import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { hostBusCall } from "../bot/lib/bus-host.js";
import {
  callHostMcpTool,
  isMcpConnectionClosedError,
} from "../bot/lib/bus-mcp-retry.js";
import { parseHostMcpJson } from "../bot/lib/mcp-result.js";
import ulgConnection, { ULG_MCP_HTTP_URL } from "../bot/host-connections/ulg.js";

describe("MCP transport (streamable HTTP)", () => {
  it("host connection uses http transport to house ulg-code path", () => {
    assert.equal("url" in ulgConnection, true);
    if ("url" in ulgConnection) {
      assert.equal(ulgConnection.type, "http");
      assert.match(ulgConnection.url, /\/mcp\/code$/);
      assert.equal(ULG_MCP_HTTP_URL, ulgConnection.url);
      assert.match(
        String(ulgConnection.headers?.authorization ?? ""),
        /\$\{ULG_MCP_TOKEN\}/,
      );
    }
  });

  it("isMcpConnectionClosedError recognizes stdio bridge failures", () => {
    assert.equal(
      isMcpConnectionClosedError(new Error("MCP error -32000: Connection closed")),
      true,
    );
    assert.equal(isMcpConnectionClosedError(new Error("Not connected")), true);
    assert.equal(isMcpConnectionClosedError(new Error("timeout")), false);
  });

  it("callHostMcpTool retries once after Connection closed", async () => {
    let attempts = 0;
    const result = await callHostMcpTool(async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("MCP error -32000: Connection closed");
      }
      return { ok: true };
    });
    assert.equal(attempts, 2);
    assert.deepEqual(result, { ok: true });
  });

  it("parseHostMcpJson decodes MCP text content to bus JSON", () => {
    const parsed = parseHostMcpJson({
      content: [{ type: "text", text: '{"turns":[{"turn_number":1}]}' }],
    }) as { turns: unknown[] };
    assert.equal(parsed.turns.length, 1);
  });

  it("hostBusCall surfaces retry success through mocked host.mcp", async () => {
    let attempts = 0;
    const host = {
      mcp: {
        callTool: async () => {
          attempts += 1;
          if (attempts === 1) {
            throw new Error("Not connected");
          }
          return { turns: [] };
        },
      },
    };
    const out = await hostBusCall(host as never, "fetch_unread", {
      to: "impresario-sdk",
      thread: "20908",
    });
    assert.equal(attempts, 2);
    assert.deepEqual(out, { turns: [] });
  });
});
