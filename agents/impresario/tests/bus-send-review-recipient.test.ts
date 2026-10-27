import assert from "node:assert/strict";
import { describe, it } from "node:test";

import busSendTool, {
  resolveReviewRequestRecipient,
} from "../bot/tools/bus_send.js";

function evalCtx() {
  return {
    session: { purpose: "eval" as const, id: "eval-test" },
    host: { mcp: { callTool: async () => ({}) } },
  } as never;
}

describe("bus_send REQUEST|review| recipient (AC1)", () => {
  it("resolveReviewRequestRecipient corrects maestro to grok-bot-gc1", () => {
    const r = resolveReviewRequestRecipient(
      "REQUEST|review|todo:reaper-amendment",
      "maestro",
    );
    assert.equal(r.to, "grok-bot-gc1");
    assert.equal(r.recipient_corrected, true);
  });

  it("execute stub returns corrected to for wrong recipient", async () => {
    const result = await busSendTool.execute!(
      {
        thread: "20901",
        to: "maestro",
        subject: "REQUEST|review|todo:reaper-amendment",
        body: "review please",
      },
      evalCtx(),
    );
    assert.equal((result as { to: string }).to, "grok-bot-gc1");
    assert.equal((result as { recipient_corrected: boolean }).recipient_corrected, true);
  });

  it("execute leaves non-review recipients unchanged", async () => {
    const result = await busSendTool.execute!(
      {
        thread: "12286",
        to: "maestro",
        subject: "DONE|todo:x|ok",
        body: "done",
      },
      evalCtx(),
    );
    assert.equal((result as { to: string }).to, "maestro");
    assert.equal(
      (result as { recipient_corrected?: boolean }).recipient_corrected,
      false,
    );
  });
});
