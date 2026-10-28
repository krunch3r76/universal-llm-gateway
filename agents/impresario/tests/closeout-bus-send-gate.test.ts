import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { primeImpresarioDeliveryRegistry } from "../bot/lib/ulg.js";
import { reviewRequestRecipient } from "../bot/tools/bus_send.js";
import {
  checkEffectiveReviewBusSend,
  closeoutReviewRecipient,
} from "../evals/lib/closeout-bus-send-gate.js";

const REGISTRY_REVIEW_MAILBOX = "grok-bot-gc1";

describe("closeout bus_send effective gate (stage 7 AC2)", () => {
  beforeEach(() => {
    primeImpresarioDeliveryRegistry({
      lane: "20901",
      reviewMailbox: REGISTRY_REVIEW_MAILBOX,
    });
  });

  it("input to omitted + effective registry review mailbox in output → pass", () => {
    const result = checkEffectiveReviewBusSend({
      toolName: "bus_send",
      args: {
        subject: "REQUEST|review|todo:reaper-amendment",
      },
      output: JSON.stringify({
        posted: true,
        stubbed: true,
        to: closeoutReviewRecipient(),
        thread: "20901",
        subject: "REQUEST|review|todo:reaper-amendment",
      }),
    });
    assert.equal(closeoutReviewRecipient(), reviewRequestRecipient());
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
        posted: true,
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

  it("unposted output → fail even when args look valid", () => {
    const result = checkEffectiveReviewBusSend({
      toolName: "bus_send",
      args: {
        subject: "REQUEST|review|todo:reaper-amendment",
        to: closeoutReviewRecipient(),
      },
      output: JSON.stringify({
        posted: false,
        to: closeoutReviewRecipient(),
        thread: "20901",
      }),
    });
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.match(result.reason, /not posted/);
    }
  });
});
