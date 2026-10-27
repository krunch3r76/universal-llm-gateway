import { defineEval, includes } from "@grok/agent-sdk/evals";

import { skipWithoutCursorCredential } from "./lib/require-cursor-credential.js";

export default defineEval({
  tags: ["smoke"],
  description: "One-line identity; no tools.",
  async test(t) {
    skipWithoutCursorCredential(t);
    await t.send("Introduce yourself in one line.");
    t.succeeded();
    t.usedNoTools();
    t.check(t.reply, includes("impresario-sdk"));
    t.check(t.reply, includes("20908"));
  },
});
