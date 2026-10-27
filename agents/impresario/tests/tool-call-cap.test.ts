import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  consumeToolCallSlot,
  impresarioToolCallCapLimit,
  resetToolCallCapForSession,
} from "../bot/lib/tool-call-cap.js";

function mockCtx(sessionId = "sess-1") {
  const store = new Map<string, unknown>();
  return {
    session: { purpose: "tool_test" as const, id: sessionId },
    host: {
      kv: {
        get: async (key: string) => store.get(key),
        put: async (key: string, value: unknown) => {
          store.set(key, value);
        },
        delete: async (key: string) => {
          store.delete(key);
        },
      },
    },
  } as never;
}

describe("tool call cap (stage 8)", () => {
  it("default limit is 5", () => {
    const prev = process.env.ULG_IMPRESARIO_TOOL_CALL_CAP;
    delete process.env.ULG_IMPRESARIO_TOOL_CALL_CAP;
    assert.equal(impresarioToolCallCapLimit(), 5);
    if (prev !== undefined) {
      process.env.ULG_IMPRESARIO_TOOL_CALL_CAP = prev;
    }
  });

  it("allows N calls then refuses with TOOL_CALL_CAP stop", async () => {
    const prev = process.env.ULG_IMPRESARIO_TOOL_CALL_CAP;
    process.env.ULG_IMPRESARIO_TOOL_CALL_CAP = "2";
    const ctx = mockCtx();
    await resetToolCallCapForSession(ctx.host, ctx.session.id);

    const first = await consumeToolCallSlot(ctx);
    assert.equal(first.allowed, true);
    const second = await consumeToolCallSlot(ctx);
    assert.equal(second.allowed, true);
    const third = await consumeToolCallSlot(ctx);
    assert.equal(third.allowed, false);
    if (!third.allowed) {
      assert.equal(third.refusal.stop, "TOOL_CALL_CAP");
      assert.equal(third.refusal.limit, 2);
    }

    if (prev === undefined) {
      delete process.env.ULG_IMPRESARIO_TOOL_CALL_CAP;
    } else {
      process.env.ULG_IMPRESARIO_TOOL_CALL_CAP = prev;
    }
  });
});
