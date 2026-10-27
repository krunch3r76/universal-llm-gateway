import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineEval, includes } from "@grok/agent-sdk/evals";

import { resetFixtureCache } from "../bot/lib/bus-fixtures.js";
import consultFixture from "./fixtures/bus/stops-consult-pending.json";
import operatorFixture from "./fixtures/bus/stops-operator-gate.json";
import rowPinnedFixture from "./fixtures/bus/stops-row-pinned.json";
import e5Fixture from "./fixtures/bus/stops-e5-concurrent.json";

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
  tags: ["stops", "e2", "e2b", "e3b", "e5"],
  cases: [
    {
      id: "row-pinned",
      description: "E2 ROW_PINNED → STOP and score_update open_stops.",
      tags: ["e2"],
      async test(t) {
        armFixtures("stops-row-pinned");
        await t.send("Handle turn.json", {
          workspaceFiles: {
            "turn.json": JSON.stringify(rowPinnedFixture.turn, null, 2),
            "score.md": rowPinnedFixture.score_md,
          },
        });
        t.succeeded();
        t.calledTool("score_update", {
          input: { section: "open_stops" },
          count: 1,
        });
        t.maxToolCalls(3);
        t.check(t.reply, includes(/^STOP\|ROW_PINNED\|todo:agent-sdk-impresario-probe/));
      },
    },
    {
      id: "consult-pending",
      description: "E2b CONSULT_PENDING steer → STOP.",
      tags: ["e2b"],
      async test(t) {
        armFixtures("stops-consult-pending");
        await t.send("Handle turn.json", {
          workspaceFiles: {
            "turn.json": JSON.stringify(consultFixture.turn, null, 2),
            "score.md": consultFixture.score_md,
          },
        });
        t.succeeded();
        t.calledTool("score_update", {
          input: { section: "open_stops" },
          count: 1,
        });
        t.maxToolCalls(3);
        t.check(
          t.reply,
          includes(/^STOP\|CONSULT_PENDING\|todo:agent-sdk-impresario-probe/),
        );
      },
    },
    {
      id: "operator-gate",
      description: "E3b OPERATOR_GATE request_ruling → STOP.",
      tags: ["e3b"],
      async test(t) {
        armFixtures("stops-operator-gate");
        await t.send("Handle turn.json", {
          workspaceFiles: {
            "turn.json": JSON.stringify(operatorFixture.turn, null, 2),
            "score.md": operatorFixture.score_md,
          },
        });
        t.succeeded();
        t.calledTool("request_ruling", { count: 1 });
        t.calledTool("score_update", {
          input: { section: "open_stops" },
          count: 1,
        });
        t.maxToolCalls(3);
        t.check(
          t.reply,
          includes(/^STOP\|OPERATOR_GATE\|todo:agent-sdk-impresario-probe/),
        );
      },
    },
    {
      id: "concurrent-commission",
      description: "E5 second commission on same work_key → ROW_PINNED STOP.",
      tags: ["e5"],
      async test(t) {
        armFixtures("stops-e5-concurrent");
        const files = {
          "turn.json": JSON.stringify(e5Fixture.turn, null, 2),
          "score.md": e5Fixture.score_md,
        };
        await t.send("Handle turn.json", { workspaceFiles: files });
        t.succeeded();
        await t.send("Handle turn.json", { workspaceFiles: files });
        t.succeeded();
        t.check(
          t.reply,
          includes(/^STOP\|ROW_PINNED\|todo:agent-sdk-impresario-probe/),
        );
      },
    },
  ],
});
