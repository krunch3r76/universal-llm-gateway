import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, it } from "node:test";

import commissionTool from "../bot/tools/commission.js";
import packetAuthorTool from "../bot/tools/packet_author.js";
import {
  fixtureLastGeneratePayload,
  resetFixtureCache,
} from "../bot/lib/bus-fixtures.js";
import {
  mergeScoreSections,
  parseStageSection,
  parseWorkerSection,
  resolveResumeFromScore,
  stageAfterCloseout,
  stageAfterVerdict,
} from "../bot/lib/score-memory.js";
import stage12Fixture from "../evals/fixtures/bus/stage12-request-changes.json" with {
  type: "json",
};

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(here, "../evals/fixtures");
const workKey = "todo:agent-sdk-impresario-probe";

function toolCtx(purpose: "tool_test" | "eval" = "tool_test") {
  return {
    session: { purpose, id: "stage12-test" },
    host: {
      mcp: {
        callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
          const { fixtureMcpCall } = await import("../bot/lib/bus-fixtures.js");
          return fixtureMcpCall(tool as "fs", args);
        },
      },
    },
  } as never;
}

describe("AC1 commission resume_of and reuse_thread on REQUEST_CHANGES", () => {
  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "stage12-request-changes";
    delete process.env.ULG_LIVE_STEER;
    resetFixtureCache();
  });

  afterEach(() => {
    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_FIXTURE_SCENARIO;
    resetFixtureCache();
  });

  it("AC1 commission passes resume_of and reuse_thread when worker terminal and verdict REQUEST_CHANGES", async () => {
    await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "fix review",
        files_expected: ["agents/impresario/bot/lib/dispatch.ts"],
        acceptance: ["pass"],
      },
      toolCtx(),
    );
    const payload = fixtureLastGeneratePayload();
    assert.ok(payload);
    assert.equal(payload.resume_of, "parent-disp-20996");
    assert.equal(payload.reuse_thread, "20996");
  });

  it("AC1 negative: non-terminal worker does not auto-resume", () => {
    const score = mergeScoreSections(stage12Fixture.scores[workKey], {
      worker: {
        dispatch_id: "live-disp",
        thread_id: "8888",
        state: "running",
      },
      stage: { verdict: "REQUEST_CHANGES" },
    });
    const resume = resolveResumeFromScore(score);
    assert.equal(resume.ok, false);
    if (!resume.ok) {
      assert.equal(resume.reason, "worker_not_terminal");
    }
  });

  it("AC1 negative: commission omits resume when score lacks terminal worker", async () => {
    process.env.ULG_FIXTURE_SCENARIO = "stops-e5-concurrent";
    resetFixtureCache();
    await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "x",
        files_expected: ["a.ts"],
        acceptance: ["ok"],
      },
      toolCtx(),
    );
    const payload = fixtureLastGeneratePayload();
    assert.ok(payload);
    assert.equal(payload.resume_of, undefined);
    assert.equal(payload.reuse_thread, undefined);
  });
});

describe("AC2 score worker and stage timestamps", () => {
  it("AC2 score fixture diff quotes worker ids and stage timestamps", () => {
    const score = stage12Fixture.scores[workKey];
    const worker = parseWorkerSection(score);
    assert.equal(worker.dispatch_id, "parent-disp-20996");
    assert.equal(worker.thread_id, "20996");
    assert.equal(worker.execution_id, "exec-parent");
    assert.equal(worker.state, "completed");
    const stage = parseStageSection(score);
    assert.equal(stage.verdict, "REQUEST_CHANGES");
    assert.match(String(stage.verdict_at), /^2026-10-27T/);

    const afterCloseout = stageAfterCloseout(stage, "2026-10-27T20:30:00.000Z");
    assert.equal(afterCloseout.done_at, "2026-10-27T20:30:00.000Z");
    const afterVerdict = stageAfterVerdict(
      afterCloseout,
      "2026-10-27T21:00:00.000Z",
      "REQUEST_CHANGES",
    );
    assert.equal(afterVerdict.verdict, "REQUEST_CHANGES");
  });
});

describe("AC3 packet_author scoped to verdict cites", () => {
  it("AC3 packet_author files_expected subset of verdict cites with one AC per review item", async () => {
    const body = stage12Fixture.turn.body as string;
    const result = await packetAuthorTool.execute!(
      { work_key: workKey, verdict_body: body },
      toolCtx(),
    );
    const r = result as {
      files_expected: string[];
      acceptance: string[];
      cites_ok: boolean;
      review_item_count: number;
    };
    assert.equal(r.review_item_count, 2);
    assert.equal(r.cites_ok, true);
    assert.deepEqual(r.files_expected.sort(), [
      "agents/impresario/bot/lib/dispatch.ts",
      "agents/impresario/bot/tools/commission.ts",
    ]);
    assert.equal(r.acceptance.length, 3);
    assert.match(r.acceptance[0]!, /^AC1:/);
    assert.match(r.acceptance[1]!, /^AC2:/);
    assert.match(r.acceptance[2]!, /^Before you post\./);
  });
});
