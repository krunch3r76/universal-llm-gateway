import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  busMailbox,
  IMPRESARIO_MAILBOX,
  callAgentBus,
  isEvalOrFixture,
} from "../lib/ulg.js";

export default defineTool({
  description:
    "Post one bus turn from the impresario mailbox. Call to acknowledge work, request review, or ask questions. Returns posted thread and subject. Does not return a designed stop.",
  inputSchema: z.object({
    thread: z.string().min(1),
    to: busMailbox,
    subject: z
      .string()
      .max(500)
      .regex(/^(DONE|FAILED|REQUEST|QUESTION|STATUS|ACK|STOP)\|/),
    body: z.string().max(2000),
    sidecar_content: z.string().max(256_000).optional(),
  }),
  effect: "write",
  dryRunResult: ({ thread, subject }) => ({
    posted: false,
    thread,
    subject,
  }),
  async execute(input, ctx) {
    if (isEvalOrFixture(ctx)) {
      return {
        posted: false,
        stubbed: true,
        from: IMPRESARIO_MAILBOX,
        ...input,
      };
    }
    const payload: Record<string, unknown> = {
      thread: input.thread,
      to: input.to,
      subject: input.subject,
      body: input.body,
      from: IMPRESARIO_MAILBOX,
    };
    if (input.sidecar_content !== undefined) {
      payload.sidecar_content = input.sidecar_content;
    }
    await callAgentBus(ctx, "send", payload);
    return { posted: true, thread: input.thread, subject: input.subject };
  },
});
