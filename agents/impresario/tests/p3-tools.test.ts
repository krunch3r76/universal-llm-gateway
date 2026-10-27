import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, beforeEach } from "node:test";

import commissionTool from "../bot/tools/commission.js";
import ledgerReadTool from "../bot/tools/ledger_read.js";
import requestRulingTool from "../bot/tools/request_ruling.js";
import {
  fixtureBusSendLogSnapshot,
  resetFixtureCache,
} from "../bot/lib/bus-fixtures.js";
import { reduceLedgerForWorkKey } from "../bot/lib/dispatch.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(here, "../evals/fixtures");
const workKey = "todo:agent-sdk-impresario-probe";

function toolCtx(purpose: "tool_test" | "eval" = "tool_test") {
  return {
    session: { purpose, id: "test-session" },
    host: {
      mcp: {
        callTool: async () => ({ stubbed: true }),
      },
    },
  } as never;
}

describe("AC1 commission schema", () => {
  it("has no model field in input schema", () => {
    const shape = (commissionTool as { inputSchema?: { shape?: Record<string, unknown> } })
      .inputSchema?.shape;
    assert.ok(shape);
    assert.equal("model" in shape!, false);
  });
});

describe("AC2 commission fixture paths", () => {
  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "stops-row-pinned";
    delete process.env.ULG_LIVE_STEER;
    resetFixtureCache();
  });

  it("returns ROW_PINNED when ledger shows in-flight row", async () => {
    const result = await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "x",
        files_expected: ["a.ts"],
        acceptance: ["pass"],
      },
      toolCtx(),
    );
    assert.equal((result as { stop?: string }).stop, "ROW_PINNED");
    assert.match(String((result as { resume_on?: string }).resume_on), /VERDICT\|/);
  });

  it("dry-run returns sha256 without live steer", async () => {
    process.env.ULG_FIXTURE_SCENARIO = "stops-e5-concurrent";
    resetFixtureCache();
    const result = await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "dry",
        files_expected: ["README.md"],
        acceptance: ["ok"],
      },
      toolCtx(),
    );
    assert.equal((result as { would_dispatch?: boolean }).would_dispatch, true);
    assert.match(
      String((result as { rendered_prompt_sha256?: string }).rendered_prompt_sha256),
      /^[0-9a-f]{64}$/,
    );
  });

  it("mocked live-shaped call when ULG_LIVE_STEER=1", async () => {
    process.env.ULG_LIVE_STEER = "1";
    delete process.env.ULG_FIXTURE_DIR;
    resetFixtureCache();
    let captured: Record<string, unknown> | undefined;
    const ctx = {
      session: { purpose: "tool_test" as const, id: "live-mock" },
      host: {
        mcp: {
          callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
            if (tool === "team_dispatch") {
              captured = args;
            }
            return { dispatch_id: "live-mock-id", posted: true };
          },
        },
      },
    } as never;
    const result = await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "live",
        files_expected: ["a.ts"],
        acceptance: ["pass"],
      },
      ctx,
    );
    assert.equal((result as { posted?: boolean }).posted, true);
    assert.equal(captured?.op, "generate");
    assert.equal(captured?.model, "cursor/composer-2.5");
  });
});

describe("AC4 ledger_read truncation", () => {
  it("returns projection under 2500 chars for large ledger", async () => {
    const rows = Array.from({ length: 80 }, (_, i) => ({
      dispatch_id: `d-${i}-${"x".repeat(40)}`,
      status: i % 5 === 0 ? "running" : "completed",
      thread_id: "99999",
      work_key: workKey,
      contract: "freeform",
    }));
    const raw = {
      work_key: workKey,
      dispatches: rows,
      house_rollup: {
        live_dispatches: rows.filter((r) => r.status === "running"),
      },
    };
    assert.ok(JSON.stringify(raw).length >= 5000);
    const { json } = reduceLedgerForWorkKey(raw, workKey);
    assert.ok(json.length < 2500);
  });

  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "stops-row-pinned";
    resetFixtureCache();
  });

  it("ledger_read tool returns compact projection", async () => {
    const result = await ledgerReadTool.execute!({ work_key: workKey }, toolCtx());
    assert.ok((result as { chars?: number }).chars! < 2500);
  });
});

describe("AC5 request_ruling", () => {
  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "stops-operator-gate";
    resetFixtureCache();
  });

  it("posts QUESTION to lane and 12286 with identical bodies", async () => {
    await requestRulingTool.execute!(
      {
        work_key: workKey,
        question: "Land on master?",
        recommended_default: "lane only",
      },
      toolCtx(),
    );
    const log = fixtureBusSendLogSnapshot();
    const questions = log.filter((e) =>
      String(e.subject).startsWith("QUESTION|"),
    );
    assert.equal(questions.length, 2);
    assert.equal(questions[0]?.body, questions[1]?.body);
    assert.equal(questions[0]?.thread, "20908");
    assert.equal(questions[1]?.thread, "12286");
  });
});
