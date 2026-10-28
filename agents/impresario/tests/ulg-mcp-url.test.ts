import assert from "node:assert/strict";
import { describe, it } from "node:test";

import ulgConnection from "../bot/host-connections/ulg.js";
import {
  ULG_MCP_HTTP_DEFAULT,
  logUlgMcpStartupResolvedHost,
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

  it("15851#3426 localhost:9338 normalizes with non-empty host", () => {
    assert.equal(
      normalizeUlgMcpHttpUrl("localhost:9338"),
      "http://localhost:9338/mcp/code",
    );
    assert.equal(ulgMcpResolvedHost("localhost:9338"), "localhost:9338");
    assert.equal(
      resolveUlgMcpHttpUrl({ ULG_MCP_HTTP_URL: "localhost:9338" }),
      "http://localhost:9338/mcp/code",
    );
  });

  it("15851#3426 host.docker.internal:9338 normalizes with non-empty host", () => {
    assert.equal(
      normalizeUlgMcpHttpUrl("host.docker.internal:9338"),
      "http://host.docker.internal:9338/mcp/code",
    );
    assert.equal(
      ulgMcpResolvedHost("host.docker.internal:9338"),
      "host.docker.internal:9338",
    );
  });

  it("scheme-full http(s) URLs pass through with path preserved", () => {
    const httpCustom = "http://example.com:9338/custom/path";
    assert.equal(normalizeUlgMcpHttpUrl(httpCustom), httpCustom);
    assert.equal(ulgMcpResolvedHost(httpCustom), "example.com:9338");

    const httpsRoot = "https://mcp.example.com/mcp/code";
    assert.equal(normalizeUlgMcpHttpUrl(httpsRoot), httpsRoot);

    const withTrailing = "http://127.0.0.1:9338/mcp/code/";
    assert.equal(
      normalizeUlgMcpHttpUrl(withTrailing),
      "http://127.0.0.1:9338/mcp/code",
    );
  });

  it("rejects non-http schemes with ULG_MCP_HTTP_URL message", () => {
    for (const bad of ["ftp://x", "file:///tmp/x"]) {
      assert.throws(
        () => normalizeUlgMcpHttpUrl(bad),
        (err: unknown) => {
          assert.ok(err instanceof Error);
          assert.match(err.message, /ULG_MCP_HTTP_URL/);
          assert.match(err.message, new RegExp(bad.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
          assert.doesNotMatch(err.message, /^TypeError:/);
          return true;
        },
      );
      assert.throws(
        () => resolveUlgMcpHttpUrl({ ULG_MCP_HTTP_URL: bad }),
        /ULG_MCP_HTTP_URL/,
      );
      assert.throws(() => ulgMcpResolvedHost(bad), /ULG_MCP_HTTP_URL/);
    }
  });

  it("rejects empty hostname with ULG_MCP_HTTP_URL message", () => {
    const bad = "http://";
    assert.throws(() => normalizeUlgMcpHttpUrl(bad), (err: unknown) => {
      assert.ok(err instanceof Error);
      assert.match(err.message, /ULG_MCP_HTTP_URL/);
      assert.match(err.message, /"http:\/\/"/);
      return true;
    });
  });

  it("startup log prints host only, never token or path secrets", () => {
    const lines: string[] = [];
    const orig = console.log;
    console.log = (...args: unknown[]) => {
      lines.push(args.map(String).join(" "));
    };
    try {
      logUlgMcpStartupResolvedHost(
        resolveUlgMcpHttpUrl({ ULG_MCP_HTTP_URL: "localhost:9338" }),
      );
    } finally {
      console.log = orig;
    }
    assert.equal(lines.length, 1);
    assert.match(lines[0]!, /resolved host=localhost:9338$/);
    assert.doesNotMatch(lines[0]!, /Bearer|token|mcp\/code/i);
  });

  it("bus-style configured_host uses resolved host for scheme-less env", () => {
    const configured = ulgMcpResolvedHost(
      resolveUlgMcpHttpUrl({ ULG_MCP_HTTP_URL: "localhost:9338" }),
    );
    assert.equal(configured, "localhost:9338");
    const meta = `${new Date().toISOString()} configured_host=${configured} err_host=? phase=wait code=?`;
    assert.match(meta, /configured_host=localhost:9338/);
    assert.doesNotMatch(meta, /Bearer|ULG_MCP_TOKEN/);
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
