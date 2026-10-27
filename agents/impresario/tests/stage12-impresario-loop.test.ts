import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, it } from "node:test";

import closeoutReadTool from "../bot/tools/closeout_read.js";
import commissionTool from "../bot/tools/commission.js";
import harvestVerdictTool from "../bot/tools/harvest_verdict.js";
import packetAuthorTool from "../bot/tools/packet_author.js";
import {
  fixtureLastGeneratePayload,
  fixtureScoreSnapshot,
  resetFixtureCache,
} from "../bot/lib/bus-fixtures.js";
import {
  mergeScoreSections,
  parseStageSection,
  parseWorkerSection,
  resolveResumeFromScore,
  validateCommissionResumeFields,
} from "../bot/lib/score-memory.js";
import stage12Fixture from "../evals/fixtures/bus/stage12-request-changes.json" with {
  type: "json",
};

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(here, "../evals/fixtures");
const workKey = "todo:agent-sdk-impresario-probe";
const reaperKey = "todo:reaper-amendment";

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
    delete process.env.ULG_FIXTURE_DENY_SCORE_WRITE;
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

  it("AC1 (M1) persist creates worker and stage sections when score lacks them", async () => {
    process.env.ULG_FIXTURE_SCENARIO = "done-20901-5";
    resetFixtureCache();
    const before = fixtureScoreSnapshot(reaperKey);
    assert.equal(before.includes("## worker"), false);
    assert.equal(before.includes("## stage"), false);

    const result = await commissionTool.execute!(
      {
        work_key: reaperKey,
        contract: "freeform",
        scope: "x",
        files_expected: ["a.ts"],
        acceptance: ["ok"],
      },
      toolCtx(),
    );
    assert.equal((result as { dispatch_id?: string }).dispatch_id, undefined);
    const after = fixtureScoreSnapshot(reaperKey);
    assert.match(after, /## worker\n/);
    assert.match(after, /## stage\n/);
    assert.match(after, /dispatch_id: fixture-/);
    assert.match(after, /commission_at:/);
  });

  it("AC1 (M1) returns dispatch_id when score persist fails after fixture dispatch", async () => {
    process.env.ULG_FIXTURE_SCENARIO = "stops-e5-concurrent";
    process.env.ULG_FIXTURE_DENY_SCORE_WRITE = "1";
    resetFixtureCache();

    const result = await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "x",
        files_expected: ["a.ts"],
        acceptance: ["ok"],
      },
      toolCtx(),
    );
    const dispatch_id = (result as { dispatch_id?: string }).dispatch_id;
    assert.match(String(dispatch_id), /^fixture-todo:agent-sdk-impresario-probe-/);
    assert.equal((result as { score_persist_ok?: boolean }).score_persist_ok, false);
    assert.equal(
      (result as { score_persist_reason?: string }).score_persist_reason,
      "score_write_failed",
    );
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

describe("AC2 production harvest writes stage timestamps", () => {
  beforeEach(() => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "done-20901-5";
    delete process.env.ULG_LIVE_STEER;
    resetFixtureCache();
  });

  afterEach(() => {
    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_FIXTURE_SCENARIO;
    delete process.env.ULG_FIXTURE_DENY_SCORE_WRITE;
    resetFixtureCache();
  });

  it("AC2 (M2) closeout_read harvest sets done_at on score", async () => {
    const dispatchId = "02a33916-2134-48d3-b32a-c90abf29c85a";
    const read = await closeoutReadTool.execute!(
      { dispatch_id: dispatchId, work_key: reaperKey },
      toolCtx(),
    );
    assert.equal((read as { score_harvest?: boolean }).score_harvest, true);
    const stage = parseStageSection(fixtureScoreSnapshot(reaperKey));
    assert.match(String(stage.done_at), /^\d{4}-\d{2}-\d{2}T/);
    const worker = parseWorkerSection(fixtureScoreSnapshot(reaperKey));
    assert.equal(worker.state, "completed");
    assert.equal(worker.dispatch_id, dispatchId);
  });

  it("AC2 (M2) harvest_verdict sets verdict_at and verdict", async () => {
    await harvestVerdictTool.execute!(
      {
        work_key: reaperKey,
        verdict_line:
          "VERDICT: REQUEST_CHANGES range=abc..def head=def",
      },
      toolCtx(),
    );
    const stage = parseStageSection(fixtureScoreSnapshot(reaperKey));
    assert.equal(stage.verdict, "REQUEST_CHANGES");
    assert.match(String(stage.verdict_at), /^\d{4}-\d{2}-\d{2}T/);
  });
});

describe("AC3 commission resume validation", () => {
  it("AC3 (M3) rejects nest_under together with resume_of", () => {
    const score = stage12Fixture.scores[workKey];
    const v = validateCommissionResumeFields({
      nest_under: "conductor-disp",
      scoreText: score,
    });
    assert.equal(v.ok, false);
    if (!v.ok) {
      assert.equal(v.reason, "nest_under_xor_resume_of");
    }
  });

  it("AC3 (M3) rejects resume_of without reuse_thread when score cannot auto-fill", () => {
    const v = validateCommissionResumeFields({
      resume_of: "orphan-disp",
      scoreText: "## state\nx\n",
    });
    assert.equal(v.ok, false);
    if (!v.ok) {
      assert.equal(v.reason, "reuse_thread_required");
    }
  });

  it("AC3 (M3) rejects alien reuse_thread for score resume pair", () => {
    const score = stage12Fixture.scores[workKey];
    const v = validateCommissionResumeFields({
      resume_of: "parent-disp-20996",
      reuse_thread: "99999",
      scoreText: score,
    });
    assert.equal(v.ok, false);
    if (!v.ok) {
      assert.equal(v.reason, "reuse_thread_mismatch");
    }
  });

  it("AC3 (M3) commission tool rejects nest_under plus auto-resume score", async () => {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = "stage12-request-changes";
    resetFixtureCache();
    const result = await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "x",
        files_expected: ["a.ts"],
        acceptance: ["ok"],
        nest_under: "cond-1",
      },
      toolCtx(),
    );
    assert.equal((result as { reason?: string }).reason, "nest_under_xor_resume_of");
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
