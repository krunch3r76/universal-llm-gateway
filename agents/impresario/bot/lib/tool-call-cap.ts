import type { HostContext } from "@grok/agent-sdk";

import type { ToolCtx } from "./ulg.js";

export type ToolCallCapStop = {
  ok: false;
  error: string;
  stop: "TOOL_CALL_CAP";
  limit: number;
  count: number;
  resume_on: string;
};

export type ToolCallCapState = {
  count: number;
  stopped: boolean;
  stop_recorded_at?: string;
};

const localCapState = new Map<string, ToolCallCapState>();

export function impresarioToolCallCapLimit(): number {
  const raw = process.env.ULG_IMPRESARIO_TOOL_CALL_CAP ?? "5";
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : 5;
}

function kvKey(sessionId: string): string {
  return `impresario:tool_call_cap:${sessionId}`;
}

async function readCapState(
  ctx: ToolCtx,
): Promise<ToolCallCapState> {
  const key = kvKey(ctx.session.id);
  if (ctx.host.kv) {
    const fromKv = (await ctx.host.kv.get(key)) as ToolCallCapState | undefined;
    return fromKv ?? { count: 0, stopped: false };
  }
  return localCapState.get(ctx.session.id) ?? { count: 0, stopped: false };
}

async function writeCapState(
  ctx: ToolCtx,
  state: ToolCallCapState,
): Promise<void> {
  const key = kvKey(ctx.session.id);
  if (ctx.host.kv) {
    await ctx.host.kv.put(key, state);
    return;
  }
  localCapState.set(ctx.session.id, state);
}

export async function resetToolCallCapForSession(
  host: HostContext,
  sessionId: string,
): Promise<void> {
  const empty = { count: 0, stopped: false } satisfies ToolCallCapState;
  if (host.kv) {
    await host.kv.put(kvKey(sessionId), empty);
    return;
  }
  localCapState.set(sessionId, empty);
}

export async function consumeToolCallSlot(
  ctx: ToolCtx,
): Promise<{ allowed: true; index: number } | { allowed: false; refusal: ToolCallCapStop }> {
  const limit = impresarioToolCallCapLimit();
  const state = await readCapState(ctx);

  if (state.count >= limit) {
    if (!state.stopped) {
      await writeCapState(ctx, {
        ...state,
        stopped: true,
        stop_recorded_at: new Date().toISOString(),
      });
    }
    return {
      allowed: false,
      refusal: {
        ok: false,
        error: `tool call cap (${limit}) exceeded`,
        stop: "TOOL_CALL_CAP",
        limit,
        count: state.count,
        resume_on: "Reply ACTED or STOP without further tool calls.",
      },
    };
  }

  const index = state.count + 1;
  await writeCapState(ctx, { ...state, count: index });
  return { allowed: true, index };
}

export async function gateToolCall<T>(
  ctx: ToolCtx,
  run: () => Promise<T>,
): Promise<T | ToolCallCapStop> {
  const slot = await consumeToolCallSlot(ctx);
  if (!slot.allowed) {
    return slot.refusal;
  }
  return run();
}
