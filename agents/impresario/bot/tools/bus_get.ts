import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { gateToolCall } from "../lib/tool-call-cap.js";
import { callAgentBusRead, truncateBody } from "../lib/ulg.js";

export default defineTool({
  description:
    "Read one bus turn in compact projection when you need text beyond turn.json. Returns turn fields with body capped at 2000 characters. Read-only; no stop.",
  inputSchema: z.object({
    thread: z.string().min(1),
    turn_number: z.union([z.number().int().positive(), z.literal("latest")]),
  }),
  effect: "read",
  async execute({ thread, turn_number }, ctx) {
    return gateToolCall(ctx, async () => {
      const result = (await callAgentBusRead(ctx, "get", {
        thread,
        turn_number,
        projection: "compact",
      })) as { turn?: { body?: string } };
      if (result.turn?.body) {
        result.turn.body = truncateBody(result.turn.body);
      }
      return result;
    });
  },
});
