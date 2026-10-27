import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

import scoreUpdateTool from "../bot/tools/score_update.js";
import {
  checkScoreSizeAfterUpdate,
  resolveScoreUri,
  SCORE_WRITE_BYTE_CAP,
  utf8ByteLength,
  windowScoreContent,
} from "../bot/lib/score-guards.js";
import {
  fixtureScoreSnapshot,
  resetFixtureCache,
} from "../bot/lib/bus-fixtures.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const scoreFixture = fs.readFileSync(
  path.join(here, "../evals/fixtures/scores/todo-agent-sdk-impresario-probe.md"),
  "utf8",
);

describe("resolveScoreUri", () => {
  it("accepts valid work keys", () => {
    const r = resolveScoreUri("todo:agent-sdk-impresario-probe");
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.match(r.uri, /impresario-scores\/todo:agent-sdk-impresario-probe\.md$/);
    }
  });

  it("rejects path escape attempts", () => {
    for (const key of ["todo:../x", "a/b", "../../etc"]) {
      const r = resolveScoreUri(key);
      assert.equal(r.ok, false);
      if (!r.ok) {
        assert.equal(r.reason, "work_key_invalid");
      }
    }
  });
});

describe("score write byte cap", () => {
  it("refuses a 4700-byte result and keeps fixture score unchanged", () => {
    process.env.ULG_FIXTURE_DIR = path.join(here, "../evals/fixtures");
    process.env.ULG_FIXTURE_SCENARIO = "done-20901-5";
    resetFixtureCache();
    const workKey = "todo:agent-sdk-impresario-probe";
    const before = fixtureScoreSnapshot(workKey);
    const body = "z".repeat(5000);
    const check = checkScoreSizeAfterUpdate(scoreFixture, "state", body);
    assert.equal(check.ok, false);
    if (!check.ok) {
      assert.equal(check.reason, "score_too_large");
      assert.ok(check.bytes > SCORE_WRITE_BYTE_CAP);
      assert.ok(check.bytes >= 4700);
      assert.equal(check.cap, SCORE_WRITE_BYTE_CAP);
    }
    assert.equal(fixtureScoreSnapshot(workKey), before);
  });

  it("allows a ~4400-byte result and writes under fixture mode", async () => {
    process.env.ULG_FIXTURE_DIR = path.join(here, "../evals/fixtures");
    process.env.ULG_FIXTURE_SCENARIO = "done-20901-5";
    resetFixtureCache();
    const workKey = "todo:agent-sdk-impresario-probe";
    let body = "a";
    let check = checkScoreSizeAfterUpdate(scoreFixture, "state", body);
    while (check.ok && check.bytes < 4400) {
      body += "a";
      check = checkScoreSizeAfterUpdate(scoreFixture, "state", body);
    }
    assert.equal(check.ok, true);
    if (check.ok) {
      assert.ok(check.bytes <= SCORE_WRITE_BYTE_CAP);
      assert.ok(check.bytes >= 4300);
    }
    const ctx = {
      session: { purpose: "tool_test" as const, id: "test" },
      host: {
        mcp: {
          callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
            const { fixtureMcpCall } = await import("../bot/lib/bus-fixtures.js");
            return fixtureMcpCall(tool as "fs", args);
          },
        },
      },
    };
    const result = await scoreUpdateTool.execute!(
      { work_key: workKey, section: "state", body },
      ctx as never,
    );
    assert.equal((result as { ok?: boolean }).ok, true);
    assert.notEqual(fixtureScoreSnapshot(workKey), scoreFixture);
  });
});

describe("score_read line window", () => {
  it("returns 60 whole lines from a 70-line fixture", () => {
    const lines = Array.from({ length: 70 }, (_, i) => `line-${i + 1}`);
    const raw = lines.join("\n");
    const { content, truncated } = windowScoreContent(raw, 60);
    assert.equal(truncated, true);
    assert.equal(content.split("\n").length, 60);
    assert.equal(content.split("\n")[0], "line-1");
    assert.equal(content.split("\n")[59], "line-60");
    assert.ok(!content.includes("line-61"));
  });

  it("does not cut mid-line when char cap applies", () => {
    const longLine = "x".repeat(200);
    const raw = Array.from({ length: 30 }, () => longLine).join("\n");
    const { content } = windowScoreContent(raw, 30, 500);
    for (const line of content.split("\n")) {
      assert.ok(line.length <= 200);
    }
    assert.ok(utf8ByteLength(content) <= 500);
  });
});
