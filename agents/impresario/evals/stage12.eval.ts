import { defineEval, includes } from "@grok/agent-sdk/evals";

import stage12Fixture from "./fixtures/bus/stage12-request-changes.json" with {
  type: "json",
};
import { skipWithoutCursorCredential } from "./lib/require-cursor-credential.js";

export default defineEval({
  tags: ["stage12", "resume", "packet_author"],
  cases: [
    {
      id: "request-changes-resume",
      description:
        "REQUEST_CHANGES without conductor commissions via packet_author with resume.",
      async test(t) {
        skipWithoutCursorCredential(t);
        await t.send("Handle turn.json", {
          workspaceFiles: {
            "turn.json": JSON.stringify(stage12Fixture.turn, null, 2),
            "score.md": stage12Fixture.scores["todo:agent-sdk-impresario-probe"],
          },
        });
        t.succeeded();
        t.calledTool("packet_author", { count: 1 });
        t.calledTool("commission", { count: 1 });
        t.notCalledTool("steer_conductor");
        t.check(t.reply, includes(/^ACTED\|/));
      },
    },
  ],
});
