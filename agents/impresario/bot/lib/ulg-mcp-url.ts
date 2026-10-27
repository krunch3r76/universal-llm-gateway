/** Default house MCP when ULG_MCP_HTTP_URL is unset (host loopback forwarder). */
export const ULG_MCP_HTTP_DEFAULT = "http://127.0.0.1:9338/mcp/code";

export function resolveUlgMcpHttpUrl(
  env: { ULG_MCP_HTTP_URL?: string } = process.env,
): string {
  const raw = env.ULG_MCP_HTTP_URL?.trim();
  return raw && raw.length > 0 ? raw : ULG_MCP_HTTP_DEFAULT;
}

export function ulgMcpResolvedHost(url: string): string {
  return new URL(url).host;
}

/** One-line startup: host only (never log token or full URL with secrets). */
export function logUlgMcpStartupResolvedHost(url: string): void {
  console.log(`[impresario/ulg-mcp] resolved host=${ulgMcpResolvedHost(url)}`);
}
