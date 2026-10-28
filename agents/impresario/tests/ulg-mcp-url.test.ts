import assert from "node:assert/strict";
import { describe, it } from "node:test";

import ulgConnection from "../bot/host-connections/ulg.js";
import {
  ULG_MCP_HTTP_DEFAULT,
  normalizeUlgMcpHttpUrl,
  resolveUlgMcpHttpUrl,
  ulgMcpResolvedHost,
} from "../bot/lib/ulg-mcp-url.js";

describe("ULG MCP HTTP URL (internal endpoint slice)", () => {
  it("AC4 env unset → loopback default", () => {
    assert.equal(resolveUlgMcpHttpUrl({}), ULG_MCP_HTTP_DEFAULT);
    assert.equal(ulgMcpResolvedHost(ULG_MCP_HTTP_DEFAULT), "127.0.0.1:9338");
  });

  it("AC4 env set → that value", () => {
    const custom = "http://172.18.0.1:9338/mcp/code";
    assert.equal(
      resolveUlgMcpHttpUrl({ ULG_MCP_HTTP_URL: custom }),
      custom,
    );
    assert.equal(ulgMcpResolvedHost(custom), "172.18.0.1:9338");
  });

  it("15851#3388 scheme-less host:port normalizes without Invalid URL", () => {
    assert.equal(
      normalizeUlgMcpHttpUrl("172.18.0.1:9338"),
      "http://172.18.0.1:9338/mcp/code",
    );
    assert.equal(
      resolveUlgMcpHttpUrl({ ULG_MCP_HTTP_URL: "172.18.0.1:9338/mcp/code" }),
      "http://172.18.0.1:9338/mcp/code",
    );
    assert.equal(ulgMcpResolvedHost("172.18.0.1:9338"), "172.18.0.1:9338");
  });

  it("AC2 AC4 Bearer ULG_MCP_TOKEN header on connection for loopback and custom URL", () => {
    assert.equal("url" in ulgConnection, true);
    if ("url" in ulgConnection) {
      assert.match(ulgConnection.url, /\/mcp\/code$/);
      assert.match(
        String(ulgConnection.headers?.authorization ?? ""),
        /\$\{ULG_MCP_TOKEN\}/,
      );
    }
  });
});
