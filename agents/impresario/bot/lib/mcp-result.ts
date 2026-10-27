type McpTextPart = { type: string; text?: string };

/** Host MCP callTool returns text JSON in `content`; fixtures return plain objects. */
export function parseHostMcpJson(result: unknown): unknown {
  if (!result || typeof result !== "object" || !("content" in result)) {
    return result;
  }
  const record = result as {
    content?: McpTextPart[];
    structuredContent?: unknown;
    isError?: boolean;
  };
  if (record.isError) {
    const text = (record.content ?? [])
      .map((part) => (part.type === "text" ? part.text ?? "" : ""))
      .join("\n");
    throw new Error(text.trim() || "MCP tool error");
  }
  if (record.structuredContent !== undefined) {
    return record.structuredContent;
  }
  const text = (record.content ?? [])
    .map((part) => (part.type === "text" ? part.text ?? "" : ""))
    .join("\n")
    .trim();
  if (!text) {
    return result;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return result;
  }
}
