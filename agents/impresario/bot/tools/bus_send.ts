import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  busMailbox,
  IMPRESARIO_MAILBOX,
  callAgentBus,
  isEvalOrFixture,
} from "../lib/ulg.js";

export const REVIEW_REQUEST_RECIPIENT = "grok-bot-gc1";

/** REQUEST|review| always posts to the stage manager mailbox. */
export function resolveReviewRequestRecipient(
  subject: string,
  to: string,
): { to: string; recipient_corrected: boolean } {
  if (subject.startsWith("REQUEST|review|")) {
    if (to !== REVIEW_REQUEST_RECIPIENT) {
      return { to: REVIEW_REQUEST_RECIPIENT, recipient_corrected: true };
    }
  }
  return { to, recipient_corrected: false };
}

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
    const { to, recipient_corrected } = resolveReviewRequestRecipient(
      input.subject,
      input.to,
    );
    const resolved = { ...input, to };
    if (isEvalOrFixture(ctx)) {
      return {
        posted: false,
        stubbed: true,
        from: IMPRESARIO_MAILBOX,
        ...resolved,
        recipient_corrected,
      };
    }
    const payload: Record<string, unknown> = {
      thread: resolved.thread,
      to: resolved.to,
      subject: resolved.subject,
      body: resolved.body,
      from: IMPRESARIO_MAILBOX,
    };
    if (input.sidecar_content !== undefined) {
      payload.sidecar_content = input.sidecar_content;
    }
    await callAgentBus(ctx, "send", payload);
    return {
      posted: true,
      thread: resolved.thread,
      subject: resolved.subject,
      to: resolved.to,
      recipient_corrected,
    };
  },
});
