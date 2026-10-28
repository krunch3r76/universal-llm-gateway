/** Default house MCP when ULG_MCP_HTTP_URL is unset (same as cursor-sdk bridge MCP_URL default). */
export const ULG_MCP_HTTP_DEFAULT = "https://mcp.k-1.me/mcp/code";

const MCP_CODE_PATH = "/mcp/code";

function hasUrlScheme(raw: string): boolean {
  return /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(raw);
}

function throwInvalidUlgMcpHttpUrl(raw: string): never {
  throw new Error(
    `Invalid ULG_MCP_HTTP_URL "${raw}": expected http(s)://host[:port][/path] or host:port`,
  );
}

function parseAndValidateUlgMcpHttpUrl(trimmed: string): URL {
  let parsed: URL;
  try {
    const withScheme = hasUrlScheme(trimmed) ? trimmed : `http://${trimmed}`;
    parsed = new URL(withScheme);
  } catch {
    throwInvalidUlgMcpHttpUrl(trimmed);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throwInvalidUlgMcpHttpUrl(trimmed);
  }
  if (!parsed.hostname) {
    throwInvalidUlgMcpHttpUrl(trimmed);
  }
  return parsed;
}

/** Accept host:port[/path] or full http(s) URL; always returns a parseable absolute URL. */
export function normalizeUlgMcpHttpUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) {
    return ULG_MCP_HTTP_DEFAULT;
  }
  const url = parseAndValidateUlgMcpHttpUrl(trimmed);
  if (url.pathname === "" || url.pathname === "/") {
    url.pathname = MCP_CODE_PATH;
  }
  return url.href.endsWith("/") && url.pathname !== "/"
    ? url.href.slice(0, -1)
    : url.href;
}

export function resolveUlgMcpHttpUrl(
  env: { ULG_MCP_HTTP_URL?: string } = process.env,
): string {
  const raw = env.ULG_MCP_HTTP_URL?.trim();
  if (!raw) {
    return ULG_MCP_HTTP_DEFAULT;
  }
  return normalizeUlgMcpHttpUrl(raw);
}

export function ulgMcpResolvedHost(url: string): string {
  return new URL(normalizeUlgMcpHttpUrl(url)).host;
}

/** One-line startup: host only (never log token or full URL with secrets). */
export function logUlgMcpStartupResolvedHost(url: string): void {
  console.log(
    `[impresario/ulg-mcp] resolved host=${ulgMcpResolvedHost(url)}`,
  );
}
