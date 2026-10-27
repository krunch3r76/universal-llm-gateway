import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, it } from "node:test";

import { resetPollGapForTest, setPollGapForTest } from "../bot/channels/bus.js";

import { resetFixtureCache } from "../bot/lib/bus-fixtures.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(here, "../evals/fixtures");

import {
  AGENT_SDK_BEARER_REQUIRED,
  requireAgentSdkBearerToken,
} from "../bot/lib/bearer.js";
import { persistLaneCursor, readLaneCursor } from "../bot/lib/bus-lane.js";
import { deliverPrepared, busWait } from "../bot/channels/bus.js";
import { IMPRESARIO_LANE } from "../bot/lib/ulg.js";
import { parseHostMcpJson } from "../bot/lib/mcp-result.js";
import { findInFlightRow } from "../bot/lib/dispatch.js";
import { WORK_KEY_ZOD_REGEX } from "../bot/lib/work-key.js";
afterEach(() => {
  resetPollGapForTest();
});

describe("fix2 poll gap N4", () => {
  it("setPollGapForTest rejects 0, negative, and NaN", () => {
    for (const bad of [0, -1, Number.NaN]) {
      assert.throws(
        () => setPollGapForTest(bad),
        /positive finite number or null/,
      );
    }
    setPollGapForTest(10);
    resetPollGapForTest();
    setPollGapForTest(null);
  });
});

describe("fix2 bus lane cursor (AC1)", () => {
  it("persists lane turn N and busWait uses after_turn=N", async () => {
    const kv = new Map<string, unknown>();
    const host = {
      kv: {
        get: async (k: string) => kv.get(k),
        put: async (k: string, v: unknown) => {
          kv.set(k, v);
        },
      },
      mcp: {
        callTool: async () => ({ content: [{ type: "text", text: "{}" }] }),
      },
    };
    await persistLaneCursor(host as never, IMPRESARIO_LANE, 42);
    assert.equal(await readLaneCursor(host as never), 42);
    let waitPayload: Record<string, unknown> | undefined;
    const hostWait = {
      ...host,
      mcp: {
        callTool: async (_n: string, tool: string, args: Record<string, unknown>) => {
          if (tool === "agent_bus") {
            waitPayload = args;
          }
          return { content: [{ type: "text", text: "{}" }] };
        },
      },
    };
    await busWait(hostWait as never, IMPRESARIO_LANE, await readLaneCursor(host as never), 5);
    const parsed = JSON.parse(String(waitPayload?.arguments)) as Record<string, unknown>;
    assert.equal(parsed.after_turn, 42);
  });
});

describe("fix2 delivery order (AC6 S6)", () => {
  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "channel-turn-20908";
    resetFixtureCache();
  });

  afterEach(() => {
    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_FIXTURE_SCENARIO;
    resetFixtureCache();
  });

  it("marks delivered only after send resolves", async () => {
    const kv = new Map<string, unknown>();
    const host = {
      kv: {
        get: async (k: string) => kv.get(k),
        put: async (k: string, v: unknown) => {
          kv.set(k, v);
        },
      },
    };
    let marked = false;
    const send = async () => {
      assert.equal(marked, false);
      throw new Error("send failed");
    };
    const prep = {
      thread: IMPRESARIO_LANE,
      turnNumber: 7,
      token: "t",
      turnJson: "{}",
      scoreMd: "",
    };
    await assert.rejects(
      () => deliverPrepared(host as never, send as never, prep),
      /send failed/,
    );
    assert.equal(kv.has(`delivered:${IMPRESARIO_LANE}#7`), false);
    marked = true;
    const sendOk = async () => ({ coalesced: false, continuationToken: "c" });
    await deliverPrepared(host as never, sendOk as never, prep);
    assert.equal(kv.get(`delivered:${IMPRESARIO_LANE}#7`), true);
    assert.equal(await readLaneCursor(host as never), 7);
  });
});

describe("fix2 MCP unwrap ledger (AC2 S5)", () => {
  it("parses SDK wrapper and ROW_PINNED sees in-flight row", () => {
    const wrapped = {
      content: [
        {
          type: "text",
          text: JSON.stringify({
            work_key: "todo:x",
            dispatches: [
              {
                dispatch_id: "d1",
                status: "running",
                work_key: "todo:x",
              },
            ],
          }),
        },
      ],
    };
    const parsed = parseHostMcpJson(wrapped) as {
      dispatches: { status: string }[];
    };
    assert.equal(findInFlightRow(parsed.dispatches)?.status, "running");
  });
});

describe("fix2 work_key schemes (AC4 S4)", () => {
  it("accepts agent-bus and plan keys; rejects probe", () => {
    assert.equal(WORK_KEY_ZOD_REGEX.test("agent-bus:15851#3141"), true);
    assert.equal(WORK_KEY_ZOD_REGEX.test("plan:x"), true);
    assert.equal(WORK_KEY_ZOD_REGEX.test("probe:x"), false);
  });
});

describe("fix2 bearer (AC6)", () => {
  it("throws AGENT_SDK_BEARER_REQUIRED when unset outside fixtures", () => {
    const prior = process.env.AGENT_SDK_BEARER_TOKEN;
    const priorFix = process.env.ULG_FIXTURE_DIR;
    delete process.env.AGENT_SDK_BEARER_TOKEN;
    delete process.env.ULG_FIXTURE_DIR;
    try {
      assert.throws(() => requireAgentSdkBearerToken(), (err: Error) => {
        return err.message === AGENT_SDK_BEARER_REQUIRED;
      });
    } finally {
      if (prior === undefined) {
        delete process.env.AGENT_SDK_BEARER_TOKEN;
      } else {
        process.env.AGENT_SDK_BEARER_TOKEN = prior;
      }
      if (priorFix === undefined) {
        delete process.env.ULG_FIXTURE_DIR;
      } else {
        process.env.ULG_FIXTURE_DIR = priorFix;
      }
    }
  });
});
