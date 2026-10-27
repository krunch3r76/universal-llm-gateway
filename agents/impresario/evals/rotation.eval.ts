import { defineEval, includes } from "@grok/agent-sdk/evals";

import doneFixture from "./fixtures/bus/done-20901-5.json";

export default defineEval({
  tags: ["rotation", "e4"],
  description: "E4 same fixture twice — same tool order and ACTED shape.",
  async test(t) {
    const files = {
      "turn.json": JSON.stringify(doneFixture.turn, null, 2),
      "score.md": doneFixture.score_md,
    };
    await t.send("Handle turn.json", { workspaceFiles: files });
    t.succeeded();
    t.toolOrder(["closeout_read", "bus_send", "score_update"]);

    await t.send("Handle turn.json", { workspaceFiles: files });
    t.succeeded();
    t.toolOrder([
      "closeout_read",
      "bus_send",
      "score_update",
      "closeout_read",
      "bus_send",
      "score_update",
    ]);
    t.check(t.reply, includes(/^ACTED\|todo:[a-z0-9-]+\|/));
  },
});
