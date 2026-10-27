import type { HostContext } from "@grok/agent-sdk";

import { fixtureMcpCall, fixturesEnabled } from "./bus-fixtures.js";
import { scoreUri, truncateBody } from "./ulg.js";

export const SCORE_CHAR_LIMIT = 4500;

export function workKeyFromTurn(turn: {
  subject?: string;
  work_key?: string;
  tags?: string[];
}): string | undefined {
  if (turn.work_key && /^(todo|friction):/.test(turn.work_key)) {
    return turn.work_key;
  }
  const subject = turn.subject ?? "";
  const m = subject.match(/\b((?:todo|friction):[a-z0-9-]+)\b/i);
  if (m) {
    return m[1]!.toLowerCase();
  }
  for (const tag of turn.tags ?? []) {
    if (/^(todo|friction):/.test(tag)) {
      return tag;
    }
  }
  return undefined;
}

export async function readScoreMarkdown(
  host: HostContext,
  workKey: string | undefined,
  fallbackScore: string,
): Promise<string> {
  if (!workKey) {
    return truncateBody(fallbackScore, SCORE_CHAR_LIMIT);
  }
  const path = scoreUri(workKey);
  if (fixturesEnabled()) {
    const result = fixtureMcpCall("fs", {
      op: "read",
      path,
      limit: 200,
    }) as { content?: string };
    const content = result.content ?? fallbackScore;
    return truncateBody(content, SCORE_CHAR_LIMIT);
  }
  try {
    const result = (await host.mcp.callTool("ulg", "fs", {
      op: "read",
      path,
      limit: 200,
    })) as unknown as { content?: string };
    const content = result.content ?? fallbackScore;
    return truncateBody(content, SCORE_CHAR_LIMIT);
  } catch {
    return truncateBody(fallbackScore, SCORE_CHAR_LIMIT);
  }
}

export function turnToJson(turn: Record<string, unknown>): string {
  const compact = {
    thread: turn.thread,
    turn_number: turn.turn_number,
    from: turn.from,
    to: turn.to,
    subject: turn.subject,
    body: truncateBody(String(turn.body ?? ""), 2000),
    work_key: turn.work_key,
  };
  return JSON.stringify(compact, null, 2);
}

export function continuationToken(
  thread: string,
  rotation: number,
): string {
  return `thread:${thread}:r${rotation}`;
}

export async function getThreadRotation(
  host: HostContext,
  thread: string,
): Promise<number> {
  const key = `bus:rotation:${thread}`;
  const v = await host.kv.get(key);
  return typeof v === "number" ? v : 0;
}

export async function setThreadRotation(
  host: HostContext,
  thread: string,
  rotation: number,
): Promise<void> {
  await host.kv.put(`bus:rotation:${thread}`, rotation);
}

export async function getThreadTurnCount(
  host: HostContext,
  thread: string,
): Promise<number> {
  const v = await host.kv.get(`bus:turn_count:${thread}`);
  return typeof v === "number" ? v : 0;
}

export async function bumpThreadTurnCount(
  host: HostContext,
  thread: string,
): Promise<{ count: number; rotated: boolean; rotation: number }> {
  const rotation = await getThreadRotation(host, thread);
  let count = (await getThreadTurnCount(host, thread)) + 1;
  let rotated = false;
  let nextRotation = rotation;
  if (count >= 12) {
    nextRotation = rotation + 1;
    await setThreadRotation(host, thread, nextRotation);
    count = 0;
    rotated = true;
  }
  await host.kv.put(`bus:turn_count:${thread}`, count);
  return { count, rotated, rotation: nextRotation };
}

export async function maybeRotateForTokens(
  host: HostContext,
  thread: string,
  inputTokens: number,
): Promise<void> {
  if (inputTokens <= 60_000) {
    return;
  }
  const rotation = await getThreadRotation(host, thread);
  await setThreadRotation(host, thread, rotation + 1);
  await host.kv.put(`bus:turn_count:${thread}`, 0);
}
