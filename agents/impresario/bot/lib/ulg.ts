import path from "node:path";
import { fileURLToPath } from "node:url";

import type { ToolContext } from "@grok/agent-sdk/tools";

import { fixtureMcpCall, fixturesEnabled } from "./bus-fixtures.js";

export type ToolCtx = ToolContext;

export const IMPRESARIO_MAILBOX =
  process.env.ULG_IMPRESARIO_MAILBOX ?? "impresario-sdk";
export const IMPRESARIO_LANE = process.env.ULG_IMPRESARIO_LANE ?? "20908";
export const COMMISSION_MODEL = "cursor/composer-2.5";

const repoRoot = path.resolve(fileURLToPath(import.meta.url), "../../../..");

export function repoScriptsPath(rel: string): string {
  return path.join(repoRoot, rel);
}

export function isEvalOrFixture(ctx: ToolCtx): boolean {
  return ctx.session.purpose === "eval" || fixturesEnabled();
}

export {
  resolveScoreUri,
  scoreUri,
  SCORE_WRITE_BYTE_CAP,
} from "./score-guards.js";

export function closeoutUri(dispatchId: string): string {
  return `workspaces://universal-llm-gateway/tmp/reviews/closeouts/${dispatchId}.md`;
}

export function truncateBody(text: string, max = 2000): string {
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max - 1)}…`;
}

export async function callAgentBus(
  ctx: ToolCtx,
  op: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureMcpCall("agent_bus", { tool: op, payload });
  }
  if (isEvalOrFixture(ctx) && op === "send") {
    return { posted: false, stubbed: true, op, ...payload };
  }
  return ctx.host.mcp.callTool("ulg", "agent_bus", {
    tool: op,
    arguments: JSON.stringify(payload),
  });
}

export async function callAgentBusRead(
  ctx: ToolCtx,
  op: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureMcpCall("agent_bus_read", { tool: op, payload });
  }
  return ctx.host.mcp.callTool("ulg", "agent_bus_read", {
    tool: op,
    arguments: JSON.stringify(payload),
  });
}

export async function callFs(
  ctx: ToolCtx,
  args: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureMcpCall("fs", args);
  }
  return ctx.host.mcp.callTool("ulg", "fs", args);
}

export function parseCloseoutExcerpt(raw: string): {
  verdict_line?: string;
  status_line?: string;
  residue?: string;
  sections: string[];
} {
  const lines = raw.split("\n");
  const sections: string[] = [];
  let verdict_line: string | undefined;
  let status_line: string | undefined;
  const bodyLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("## effects_manifest")) {
      break;
    }
    if (line.startsWith("## ")) {
      sections.push(line.slice(3).trim());
    }
    if (/^VERDICT:/i.test(line)) {
      verdict_line = line.trim();
    }
    if (/^status:/i.test(line)) {
      status_line = line.trim();
    }
    bodyLines.push(line);
    if (bodyLines.length >= 60) {
      break;
    }
  }
  const residue = bodyLines.join("\n").trim().slice(0, 4500);
  return { verdict_line, status_line, residue, sections };
}
