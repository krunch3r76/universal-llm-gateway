import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineEval, includes, satisfies } from "@grok/agent-sdk/evals";

import doneFixture from "./fixtures/bus/done-20901-5.json";
import { withFixtureScenario } from "./lib/arm-fixtures.js";
import { e4RuleFromTurn } from "./lib/e4-from-turn.js";
import type { E4RuleResult } from "./lib/e4-rule-check.js";
import { skipWithoutCursorCredential } from "./lib/require-cursor-credential.js";

const fixtureRoot = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
);

const WORK_KEY = "todo:reaper-amendment";

export default defineEval({
  tags: ["rotation", "e4"],
  description: "E4 same fixture twice — K109 rule check each run.",
  async test(t) {
    skipWithoutCursorCredential(t);
    await withFixtureScenario(fixtureRoot, "done-20901-5", async () => {
      const files = {
        "turn.json": JSON.stringify(doneFixture.turn, null, 2),
        "score.md": doneFixture.score_md,
      };
      const turn1 = await t.send("Handle turn.json", { workspaceFiles: files });
      turn1.succeeded();
      const e4Run1 = e4RuleFromTurn(turn1, WORK_KEY);
      t.check(
        e4Run1,
        satisfies(
          (v) => (v as E4RuleResult).ok === true,
          "E4 rule check run 1",
        ),
      );
      turn1.maxToolCalls(5);

      const turn2 = await t.send("Handle turn.json", { workspaceFiles: files });
      turn2.succeeded();
      const e4Run2 = e4RuleFromTurn(turn2, WORK_KEY);
      t.check(
        e4Run2,
        satisfies(
          (v) => (v as E4RuleResult).ok === true,
          "E4 rule check run 2",
        ),
      );
      turn2.maxToolCalls(5);
      t.check(t.reply, includes(/^ACTED\|todo:[a-z0-9-]+\|/));
    });
  },
});
