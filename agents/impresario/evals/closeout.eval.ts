import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineEval, includes } from "@grok/agent-sdk/evals";

import { resetFixtureCache } from "../bot/lib/bus-fixtures.js";
import doneFixture from "./fixtures/bus/done-20901-5.json";
import { skipWithoutCursorCredential } from "./lib/require-cursor-credential.js";

const fixtureRoot = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
);

function armFixtures(scenario: string): void {
  process.env.ULG_FIXTURE_DIR = fixtureRoot;
  process.env.ULG_FIXTURE_SCENARIO = scenario;
  resetFixtureCache();
}

export default defineEval({
  tags: ["closeout", "e1"],
  description: "E1 DONE closeout → REQUEST review, no commission.",
  async test(t) {
    skipWithoutCursorCredential(t);
    armFixtures("done-20901-5");
    await t.send("Handle turn.json", {
      workspaceFiles: {
        "turn.json": JSON.stringify(doneFixture.turn, null, 2),
        "score.md": doneFixture.score_md,
      },
    });
    t.succeeded();
    t.calledTool("closeout_read", { count: 1 });
    t.calledTool("bus_send", {
      input: {
        subject: /^REQUEST\|review\|todo:/,
        to: "grok-bot-gc1",
      },
    });
    t.notCalledTool("commission");
    t.notCalledTool("bus_get");
    t.maxToolCalls(5);
    t.check(t.reply, includes(/^ACTED\|todo:[a-z0-9-]+\|/));
  },
});
