import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  checkEffectiveReviewBusSend,
  CLOSEOUT_REVIEW_RECIPIENT,
} from "../evals/lib/closeout-bus-send-gate.js";

describe("closeout bus_send effective gate (stage 7 AC2)", () => {
  it("input to omitted + effective grok-bot-gc1 in output → pass", () => {
    const result = checkEffectiveReviewBusSend({
      toolName: "bus_send",
      args: {
        subject: "REQUEST|review|todo:reaper-amendment",
      },
      output: JSON.stringify({
        stubbed: true,
        to: CLOSEOUT_REVIEW_RECIPIENT,
        thread: "20901",
        subject: "REQUEST|review|todo:reaper-amendment",
      }),
    });
    assert.deepEqual(result, { ok: true });
  });

  it("effective recipient maestro in output → fail", () => {
    const result = checkEffectiveReviewBusSend({
      toolName: "bus_send",
      args: {
        subject: "REQUEST|review|todo:reaper-amendment",
        to: "maestro",
      },
      output: JSON.stringify({
        stubbed: true,
        to: "maestro",
        thread: "20901",
        subject: "REQUEST|review|todo:reaper-amendment",
      }),
    });
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.match(result.reason, /effective recipient maestro/);
    }
  });
});
