import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, beforeEach } from "node:test";

import {
  fixtureBusSendLogSnapshot,
  resetFixtureCache,
} from "../bot/lib/bus-fixtures.js";
import { mirrorTurnFailed } from "../bot/hooks/bus_mirror.js";
import { postDailyUsageStatus } from "../bot/lib/daily-usage-report.js";
import {
  maybePostSeatRegistration,
  seatRegistrationBody,
  turnAddressedToMailbox,
} from "../bot/lib/run-shape.js";
import { modelTurnsEnabled } from "../bot/lib/ulg.js";
import { readUsageRollup, recordGatedTurn } from "../bot/lib/usage-rollup.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(here, "../evals/fixtures");

function memoryHost() {
  const store = new Map<string, unknown>();
  return {
    kv: {
      async get(key: string) {
        return store.get(key);
      },
      async put(key: string, value: unknown) {
        store.set(key, value);
      },
      async delete(key: string) {
        store.delete(key);
      },
    },
    mcp: {
      callTool: async () => ({ stubbed: true }),
    },
    _store: store,
  };
}

describe("AC3 ULG_MODEL_TURNS gate", () => {
  beforeEach(() => {
    delete process.env.ULG_MODEL_TURNS;
    delete process.env.CURSOR_API_KEY;
  });

  it("defaults model turns off without CURSOR_API_KEY", () => {
    assert.equal(modelTurnsEnabled(), false);
  });

  it("rejects wrong mailbox", () => {
    assert.equal(turnAddressedToMailbox({ to: "maestro" }), false);
    assert.equal(turnAddressedToMailbox({ to: "impresario-sdk" }), true);
  });
});

describe("AC4 seat registration cap", () => {
  it("posts at most once per 60s", async () => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "channel-turn-20908";
    resetFixtureCache();
    const host = memoryHost();
    const first = await maybePostSeatRegistration(host as never, "/tmp/state");
    const second = await maybePostSeatRegistration(host as never, "/tmp/state");
    assert.equal(first, true);
    assert.equal(second, false);
    const log = fixtureBusSendLogSnapshot();
    assert.equal(log.length, 1);
    assert.match(String(log[0]?.body), /TYPE: SEAT_REGISTRATION/);
    assert.match(seatRegistrationBody("/tmp/state"), /state-root \/tmp\/state/);
    delete process.env.ULG_FIXTURE_DIR;
    resetFixtureCache();
  });
});

describe("AC6 failure mirror and daily usage fixtures", () => {
  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "channel-turn-20908";
    resetFixtureCache();
  });

  it("mirrors FAILED| to lane and maestro thread", async () => {
    const host = memoryHost();
    await mirrorTurnFailed(host as never, "ses_test123", "boom");
    const log = fixtureBusSendLogSnapshot();
    assert.equal(log.length, 2);
    assert.match(String(log[0]?.subject), /^FAILED\|impresario\|ses_test123$/);
    assert.equal(log[0]?.thread, "20908");
    assert.equal(log[1]?.thread, "12286");
    assert.equal(log[0]?.to, "maestro");
  });

  it("dry-runs daily usage STATUS from kv rollup", async () => {
    const host = memoryHost();
    await recordGatedTurn(host as never);
    await postDailyUsageStatus(host as never);
    const log = fixtureBusSendLogSnapshot();
    const usage = log.find((e) => String(e.subject).includes("STATUS|usage|impresario"));
    assert.ok(usage);
    assert.match(String(usage?.body), /gated_bus_turns=1/);
    const rollup = await readUsageRollup(host as never);
    assert.equal(rollup.gatedTurns, 1);
  });
});
