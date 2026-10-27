/** Errors from a dead stdio bridge or dropped MCP session (see P4 F-1). */
export function isMcpConnectionClosedError(err: unknown): boolean {
  const parts: string[] = [];
  if (err instanceof Error) {
    parts.push(err.message);
    if (typeof err.cause === "object" && err.cause !== null && "message" in err.cause) {
      parts.push(String((err.cause as Error).message));
    }
  } else {
    parts.push(String(err));
  }
  const msg = parts.join(" ");
  return (
    /Connection closed/i.test(msg) ||
    /Not connected/i.test(msg) ||
    /-32000/.test(msg)
  );
}

export async function callHostMcpTool(
  call: () => Promise<unknown>,
): Promise<unknown> {
  try {
    return await call();
  } catch (err) {
    if (!isMcpConnectionClosedError(err)) {
      throw err;
    }
    return await call();
  }
}
