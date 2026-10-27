import { defineEval, includes } from "@grok/agent-sdk/evals";

import factFixture from "./fixtures/bus/fact-12286-5080.json";

export default defineEval({
  tags: ["fact", "e3"],
  description: "E3 Kaywan FACT → rulings score_update, NOTED.",
  async test(t) {
    await t.send("Handle turn.json", {
      workspaceFiles: {
        "turn.json": JSON.stringify(factFixture.turn, null, 2),
        "score.md": factFixture.score_md,
      },
    });
    t.succeeded();
    t.calledTool("score_update", {
      input: { section: "rulings" },
      count: 1,
    });
    t.notCalledTool("commission");
    t.notCalledTool("steer_conductor");
    t.notCalledTool("bus_send");
    t.maxToolCalls(2);
    t.check(t.reply, includes(/^NOTED\|/));
  },
});
