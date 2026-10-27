import { defineEval, includes } from "@grok/agent-sdk/evals";

import verdictFixture from "./fixtures/bus/verdict-15851-3122.json";

export default defineEval({
  tags: ["verdict", "e2"],
  cases: [
    {
      id: "conductor",
      description: "E2 REQUEST_CHANGES with conductor named in score.",
      async test(t) {
        await t.send("Handle turn.json", {
          workspaceFiles: {
            "turn.json": JSON.stringify(verdictFixture.turn, null, 2),
            "score.md": verdictFixture.score_md,
          },
        });
        t.succeeded();
        t.calledTool("score_update");
        t.notCalledTool("commission");
        t.check(t.reply, includes(/^ACTED\|/));
      },
    },
  ],
});
