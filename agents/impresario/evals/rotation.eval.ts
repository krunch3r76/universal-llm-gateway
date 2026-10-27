import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  defineEval,
  deriveRunFacts,
  includes,
  satisfies,
} from "@grok/agent-sdk/evals";

import { resetFixtureCache } from "../bot/lib/bus-fixtures.js";
import doneFixture from "./fixtures/bus/done-20901-5.json";
import {
  checkE4CloseoutRun,
  type E4RuleResult,
} from "./lib/e4-rule-check.js";
import { skipWithoutCursorCredential } from "./lib/require-cursor-credential.js";

const fixtureRoot = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
);

const WORK_KEY = "todo:reaper-amendment";

function armFixtures(scenario: string): void {
  process.env.ULG_FIXTURE_DIR = fixtureRoot;
  process.env.ULG_FIXTURE_SCENARIO = scenario;
  resetFixtureCache();
}

function e4FromTurnEvents(events: readonly { type: string; data?: unknown }[]) {
  const facts = deriveRunFacts(events);
  return checkE4CloseoutRun(
    facts.toolCalls.map((c) => ({
      toolName: c.toolName,
      args: (c.input ?? {}) as Record<string, unknown>,
    })),
    WORK_KEY,
  );
}

export default defineEval({
  tags: ["rotation", "e4"],
  description: "E4 same fixture twice — K109 rule check each run.",
  async test(t) {
    skipWithoutCursorCredential(t);
    armFixtures("done-20901-5");
    const files = {
      "turn.json": JSON.stringify(doneFixture.turn, null, 2),
      "score.md": doneFixture.score_md,
    };
    const turn1 = await t.send("Handle turn.json", { workspaceFiles: files });
    turn1.succeeded();
    const e4Run1 = e4FromTurnEvents(turn1.events);
    t.check(
      e4Run1,
      satisfies(
        (v) => (v as E4RuleResult).ok === true,
        "E4 rule check run 1",
      ),
    );

    const turn2 = await t.send("Handle turn.json", { workspaceFiles: files });
    turn2.succeeded();
    const e4Run2 = e4FromTurnEvents(turn2.events);
    t.check(
      e4Run2,
      satisfies(
        (v) => (v as E4RuleResult).ok === true,
        "E4 rule check run 2",
      ),
    );
    t.check(t.reply, includes(/^ACTED\|todo:[a-z0-9-]+\|/));
  },
});
