import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { callAgentBusRead, truncateBody } from "../lib/ulg.js";

export default defineTool({
  description:
    "Read one bus turn (compact projection). Body is capped at 2000 characters.",
  inputSchema: z.object({
    thread: z.string().min(1),
    turn_number: z.union([z.number().int().positive(), z.literal("latest")]),
  }),
  effect: "read",
  async execute({ thread, turn_number }, ctx) {
    const result = (await callAgentBusRead(ctx, "get", {
      thread,
      turn_number,
      projection: "compact",
    })) as { turn?: { body?: string } };
    if (result.turn?.body) {
      result.turn.body = truncateBody(result.turn.body);
    }
    return result;
  },
});
