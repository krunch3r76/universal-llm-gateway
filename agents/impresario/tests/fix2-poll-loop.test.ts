import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, it } from "node:test";

import { resetFixtureCache } from "../bot/lib/bus-fixtures.js";
import { LAST_LANE_TURN_KEY, readLaneCursor } from "../bot/lib/bus-lane.js";
import {
  pollOnce,
  resetPollLoopForTest,
  runPollLoop,
  signalPollLoopStop,
} from "../bot/channels/bus.js";
import { IMPRESARIO_LANE } from "../bot/lib/ulg.js";

const here = path.dirname(fileURLToPath(import.meta.url));

function writePollFixture(dir: string, turns: { turn_number: number }[]): void {
  const busDir = path.join(dir, "bus");
  fs.mkdirSync(busDir, { recursive: true });
  const body = {
    scenario: "poll-nits",
    turns: turns.map((t) => ({
      thread: IMPRESARIO_LANE,
      turn_number: t.turn_number,
      from: "maestro",
      to: "impresario-sdk",
      subject: "STATUS|probe|poll nit",
      body: `turn ${t.turn_number}`,
      work_key: "todo:agent-sdk-impresario-probe",
      status: "open",
    })),
    turn: {
      thread: IMPRESARIO_LANE,
      turn_number: turns[0]!.turn_number,
      from: "maestro",
      to: "impresario-sdk",
      subject: "STATUS|probe|poll nit",
      body: "first",
      work_key: "todo:agent-sdk-impresario-probe",
      status: "open",
    },
    score_md: "## state\nprobe\n",
    closeouts: {},
  };
  fs.writeFileSync(
    path.join(busDir, "poll-nits.json"),
    JSON.stringify(body, null, 2),
  );
}

function kvMap(
  opts: {
    getThrowKeys?: Set<string>;
    putThrowKeys?: Set<string>;
  } = {},
): Map<string, unknown> {
  const store = new Map<string, unknown>();
  return store;
}

function hostFromKv(
  store: Map<string, unknown>,
  opts: {
    getThrowKeys?: Set<string>;
    putThrowKeys?: Set<string>;
  } = {},
): { kv: HostKv; host: { kv: HostKv } } {
  const getThrowKeys = opts.getThrowKeys ?? new Set<string>();
  const putThrowKeys = opts.putThrowKeys ?? new Set<string>();
  const kv: HostKv = {
    get: async (k: string) => {
      if (getThrowKeys.has(k)) {
        throw new Error(`kv.get failed: ${k}`);
      }
      return store.get(k);
    },
    put: async (k: string, v: unknown) => {
      if (putThrowKeys.has(k)) {
        throw new Error(`kv.put failed: ${k}`);
      }
      store.set(k, v);
    },
  };
  return { kv, host: { kv } };
}

type HostKv = {
  get: (k: string) => Promise<unknown>;
  put: (k: string, v: unknown) => Promise<void>;
};

describe("fix2 nits N1 lane cursor get guard", () => {
  let fixtureDir: string;
  const envRestore: Record<string, string | undefined> = {};

  beforeEach(() => {
    fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-poll-"));
    writePollFixture(fixtureDir, [{ turn_number: 7 }]);
    envRestore.ULG_FIXTURE_DIR = process.env.ULG_FIXTURE_DIR;
    envRestore.ULG_FIXTURE_SCENARIO = process.env.ULG_FIXTURE_SCENARIO;
    envRestore.ULG_MODEL_TURNS = process.env.ULG_MODEL_TURNS;
    envRestore.CURSOR_API_KEY = process.env.CURSOR_API_KEY;
    process.env.ULG_FIXTURE_DIR = fixtureDir;
    process.env.ULG_FIXTURE_SCENARIO = "poll-nits";
    delete process.env.ULG_MODEL_TURNS;
    delete process.env.CURSOR_API_KEY;
    resetFixtureCache();
  });

  afterEach(() => {
    for (const [k, v] of Object.entries(envRestore)) {
      if (v === undefined) {
        delete process.env[k];
      } else {
        process.env[k] = v;
      }
    }
    resetFixtureCache();
    fs.rmSync(fixtureDir, { recursive: true, force: true });
  });

  it("pollOnce delivers when lane kv.get throws (cursor 0, fetch proceeds)", async () => {
    const store = kvMap();
    const { host } = hostFromKv(store, {
      getThrowKeys: new Set([LAST_LANE_TURN_KEY]),
    });
    assert.equal(await readLaneCursor(host as never), 0);
    let sendCalls = 0;
    const args = {
      host: host as never,
      send: async () => {
        sendCalls++;
        return { coalesced: false, continuationToken: "c" };
      },
    };
    await pollOnce(args as never, "");
    assert.equal(
      store.get(`delivered:${IMPRESARIO_LANE}#7`),
      true,
      "turn 7 marked delivered after pollOnce",
    );
    assert.equal(sendCalls, 0, "gated path without model turns");
  });
});

describe("fix2 nits N2 lane cursor put guard", () => {
  let fixtureDir: string;
  const envRestore: Record<string, string | undefined> = {};

  beforeEach(() => {
    fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-poll-"));
    writePollFixture(fixtureDir, [{ turn_number: 8 }]);
    envRestore.ULG_FIXTURE_DIR = process.env.ULG_FIXTURE_DIR;
    envRestore.ULG_FIXTURE_SCENARIO = process.env.ULG_FIXTURE_SCENARIO;
    envRestore.ULG_MODEL_TURNS = process.env.ULG_MODEL_TURNS;
    envRestore.CURSOR_API_KEY = process.env.CURSOR_API_KEY;
    process.env.ULG_FIXTURE_DIR = fixtureDir;
    process.env.ULG_FIXTURE_SCENARIO = "poll-nits";
    process.env.ULG_MODEL_TURNS = "1";
    process.env.CURSOR_API_KEY = "fixture-test-key";
    resetFixtureCache();
  });

  afterEach(() => {
    for (const [k, v] of Object.entries(envRestore)) {
      if (v === undefined) {
        delete process.env[k];
      } else {
        process.env[k] = v;
      }
    }
    resetFixtureCache();
    fs.rmSync(fixtureDir, { recursive: true, force: true });
  });

  it("send runs once across two polls when lane kv.put throws", async () => {
    const store = kvMap();
    const { host } = hostFromKv(store, {
      putThrowKeys: new Set([LAST_LANE_TURN_KEY]),
    });
    let sendCalls = 0;
    const args = {
      host: host as never,
      send: async () => {
        sendCalls++;
        return { coalesced: false, continuationToken: "c" };
      },
    };
    await pollOnce(args as never, "");
    await pollOnce(args as never, "");
    assert.equal(sendCalls, 1, "turn not re-sent when delivered marker persists");
  });
});

describe("fix2 nits N3 pollOnce and runPollLoop", () => {
  let fixtureDir: string;
  const envRestore: Record<string, string | undefined> = {};

  beforeEach(() => {
    fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-poll-"));
    writePollFixture(fixtureDir, [{ turn_number: 7 }, { turn_number: 9 }]);
    envRestore.ULG_FIXTURE_DIR = process.env.ULG_FIXTURE_DIR;
    envRestore.ULG_FIXTURE_SCENARIO = process.env.ULG_FIXTURE_SCENARIO;
    envRestore.ULG_MODEL_TURNS = process.env.ULG_MODEL_TURNS;
    envRestore.CURSOR_API_KEY = process.env.CURSOR_API_KEY;
    envRestore.ULG_BUS_POLL_GAP_MS = process.env.ULG_BUS_POLL_GAP_MS;
    process.env.ULG_FIXTURE_DIR = fixtureDir;
    process.env.ULG_FIXTURE_SCENARIO = "poll-nits";
    delete process.env.ULG_MODEL_TURNS;
    delete process.env.CURSOR_API_KEY;
    resetFixtureCache();
  });

  afterEach(async () => {
    signalPollLoopStop();
    for (const [k, v] of Object.entries(envRestore)) {
      if (v === undefined) {
        delete process.env[k];
      } else {
        process.env[k] = v;
      }
    }
    resetFixtureCache();
    fs.rmSync(fixtureDir, { recursive: true, force: true });
  });

  it("pollOnce delivers turns 7 and 9 and persists lane cursor 9", async () => {
    const store = kvMap();
    const { host } = hostFromKv(store);
    const args = {
      host: host as never,
      send: async () => ({ coalesced: false, continuationToken: "c" }),
    };
    await pollOnce(args as never, "");
    assert.equal(store.get(`delivered:${IMPRESARIO_LANE}#7`), true);
    assert.equal(store.get(`delivered:${IMPRESARIO_LANE}#9`), true);
    assert.equal(await readLaneCursor(host as never), 9);
  });

  it("runPollLoop runs at least two iterations then exits on stop signal", async () => {
    process.env.ULG_BUS_POLL_GAP_MS = "1";
    resetPollLoopForTest();
    const store = kvMap();
    const { kv: baseKv } = hostFromKv(store);
    let laneCursorReads = 0;
    const host = {
      kv: {
        get: async (k: string) => {
          if (k === LAST_LANE_TURN_KEY) {
            laneCursorReads++;
          }
          return baseKv.get(k);
        },
        put: baseKv.put,
      },
    };
    const args = {
      host: host as never,
      send: async () => ({ coalesced: false, continuationToken: "c" }),
    };
    const loopDone = runPollLoop(args as never);
    const deadline = Date.now() + 500;
    while (laneCursorReads < 2 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 5));
    }
    signalPollLoopStop();
    await loopDone;
    assert.ok(
      laneCursorReads >= 2,
      "runPollLoop invoked pollOnce at least twice",
    );
  });
});
