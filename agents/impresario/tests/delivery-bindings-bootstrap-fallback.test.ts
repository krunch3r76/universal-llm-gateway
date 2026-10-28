import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import busChannel from "../bot/channels/bus.js";
import {
  resetPollGapForTest,
  resetPollLoopForTest,
  setPollGapForTest,
  signalPollLoopStop,
} from "../bot/channels/bus.js";
import {
  bootstrapImpresarioRegistry,
  getImpresarioLane,
  impresarioReviewMailbox,
  resetImpresarioDeliveryRegistryForTest,
} from "../bot/lib/ulg.js";

const envRestore: Record<string, string | undefined> = {};

function saveEnv(keys: string[]): void {
  for (const k of keys) {
    envRestore[k] = process.env[k];
  }
}

function restoreEnv(keys: string[]): void {
  for (const k of keys) {
    if (envRestore[k] === undefined) {
      delete process.env[k];
    } else {
      process.env[k] = envRestore[k];
    }
  }
}

function channelToolCtx(host: { kv: unknown; mcp: unknown }) {
  return {
    session: { purpose: "channel" as const, id: "impresario-bus" },
    host,
  } as never;
}

describe("delivery-bindings bootstrap fallback (15851#3439)", () => {
  const envKeys = [
    "ULG_IMPRESARIO_LANE",
    "ULG_IMPRESARIO_REVIEW_MAILBOX",
    "ULG_FIXTURE_DIR",
    "ULG_FIXTURE_SCENARIO",
    "ULG_BUS_MODE",
    "AGENT_SDK_BEARER_TOKEN",
  ];

  beforeEach(() => {
    saveEnv(envKeys);
    resetImpresarioDeliveryRegistryForTest();
    resetPollLoopForTest();
    resetPollGapForTest();
  });

  afterEach(() => {
    restoreEnv(envKeys);
    resetImpresarioDeliveryRegistryForTest();
    resetPollLoopForTest();
    resetPollGapForTest();
    signalPollLoopStop();
  });

  it("bootstrap skips paste resolve when ULG_IMPRESARIO_REVIEW_MAILBOX is set", async () => {
    process.env.ULG_IMPRESARIO_LANE = "21062";
    process.env.ULG_IMPRESARIO_REVIEW_MAILBOX = "review-mailbox-env";
    delete process.env.ULG_FIXTURE_DIR;

    const resolveCalls: { intent?: string }[] = [];
    const host = {
      kv: {
        get: async () => undefined,
        put: async () => undefined,
      },
      mcp: {
        callTool: async (
          _ns: string,
          mcpTool: string,
          args: Record<string, unknown>,
        ) => {
          if (mcpTool === "agent_bus_read" && args.tool === "delivery_bindings_resolve") {
            const payload = JSON.parse(String(args.arguments)) as {
              intent?: string;
            };
            resolveCalls.push({ intent: payload.intent });
            throw new Error("resolve must not run for paste when env review mailbox set");
          }
          if (mcpTool === "agent_bus" && args.tool === "subscribe") {
            return { content: [{ type: "text", text: "{}" }] };
          }
          return { content: [{ type: "text", text: "{}" }] };
        },
      },
    };

    await bootstrapImpresarioRegistry(channelToolCtx(host));
    assert.equal(impresarioReviewMailbox(), "review-mailbox-env");
    assert.equal(
      resolveCalls.some((c) => c.intent === "paste"),
      false,
      "paste intent resolve must be skipped",
    );
  });

  it("onStart runs poll loop on bootstrap failure using ULG_IMPRESARIO_LANE", async () => {
    process.env.ULG_IMPRESARIO_LANE = "21062";
    delete process.env.ULG_IMPRESARIO_REVIEW_MAILBOX;
    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_BUS_MODE;
    process.env.AGENT_SDK_BEARER_TOKEN = "test-bearer";

    let fetchUnreadThread: string | undefined;
    const host = {
      kv: {
        get: async () => undefined,
        put: async () => undefined,
      },
      mcp: {
        callTool: async (
          _ns: string,
          mcpTool: string,
          args: Record<string, unknown>,
        ) => {
          if (mcpTool === "agent_bus" && args.tool === "subscribe") {
            throw new Error("bootstrap subscribe failed");
          }
          if (mcpTool === "agent_bus" && args.tool === "fetch_unread") {
            const payload = JSON.parse(String(args.arguments)) as {
              thread?: string;
            };
            fetchUnreadThread = payload.thread;
            signalPollLoopStop();
            return {
              content: [{ type: "text", text: JSON.stringify({ turns: [] }) }],
            };
          }
          if (mcpTool === "agent_bus" && args.tool === "send") {
            return { content: [{ type: "text", text: "{}" }] };
          }
          return { content: [{ type: "text", text: "{}" }] };
        },
      },
    };

    setPollGapForTest(5);
    const waitUntilTasks: Promise<void>[] = [];
    busChannel.onStart({
      host: host as never,
      send: async () => ({ coalesced: false }),
      waitUntil: (task: Promise<void>) => {
        waitUntilTasks.push(task);
      },
    } as never);

    assert.equal(waitUntilTasks.length, 1);
    await waitUntilTasks[0];

    assert.equal(getImpresarioLane(), "21062");
    assert.equal(fetchUnreadThread, "21062");
  });
});
