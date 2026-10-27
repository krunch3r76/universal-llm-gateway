import fs from "node:fs";
import path from "node:path";

import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  busMailbox,
  IMPRESARIO_MAILBOX,
  callAgentBus,
  isEvalOrFixture,
} from "../lib/ulg.js";

export const REVIEW_REQUEST_RECIPIENT = "grok-bot-gc1";

/** Read `thread` from seeded session workspace turn.json when present. */
export function readTurnThreadFromWorkspace(workspaceDir: string): string | null {
  try {
    const raw = fs.readFileSync(path.join(workspaceDir, "turn.json"), "utf8");
    const turn = JSON.parse(raw) as { thread?: unknown };
    if (typeof turn.thread === "string" && turn.thread.trim() !== "") {
      return turn.thread.trim();
    }
  } catch {
    /* no turn.json or invalid JSON */
  }
  return null;
}

/** REQUEST|review| always posts to the stage manager mailbox. */
export function resolveReviewRequestRecipient(
  subject: string,
  to: string | undefined,
): { to: string; recipient_corrected: boolean } {
  if (subject.startsWith("REQUEST|review|")) {
    const resolved = to ?? REVIEW_REQUEST_RECIPIENT;
    if (to !== undefined && to !== REVIEW_REQUEST_RECIPIENT) {
      return { to: REVIEW_REQUEST_RECIPIENT, recipient_corrected: true };
    }
    return { to: resolved, recipient_corrected: false };
  }
  if (to === undefined) {
    throw new Error("resolveReviewRequestRecipient: to required for non-review subject");
  }
  return { to, recipient_corrected: false };
}

export type ResolveBusSendResult =
  | {
      ok: true;
      thread: string;
      to: string;
      recipient_corrected: boolean;
      thread_defaulted: boolean;
    }
  | { ok: false; error: string };

export function resolveBusSendInput(input: {
  subject: string;
  thread?: string;
  to?: string;
  workspaceDir?: string;
}): ResolveBusSendResult {
  let thread = input.thread?.trim();
  let thread_defaulted = false;
  if (!thread) {
    const fromTurn =
      input.workspaceDir !== undefined
        ? readTurnThreadFromWorkspace(input.workspaceDir)
        : null;
    if (fromTurn) {
      thread = fromTurn;
      thread_defaulted = true;
    }
  }
  if (!thread) {
    return { ok: false, error: "thread is required when turn.json is missing or has no thread" };
  }

  if (input.subject.startsWith("REQUEST|review|")) {
    const { to, recipient_corrected } = resolveReviewRequestRecipient(
      input.subject,
      input.to,
    );
    return { ok: true, thread, to, recipient_corrected, thread_defaulted };
  }

  if (input.to === undefined) {
    return { ok: false, error: "to is required for this subject head" };
  }
  return {
    ok: true,
    thread,
    to: input.to,
    recipient_corrected: false,
    thread_defaulted,
  };
}

export default defineTool({
  description:
    "Post one bus turn from the impresario mailbox. Call to acknowledge work, request review, or ask questions. Returns posted thread and subject. Does not return a designed stop.",
  inputSchema: z.object({
    thread: z.string().min(1).optional(),
    to: busMailbox.optional(),
    subject: z
      .string()
      .max(500)
      .regex(/^(DONE|FAILED|REQUEST|QUESTION|STATUS|ACK|STOP)\|/),
    body: z.string().max(2000),
    sidecar_content: z.string().max(256_000).optional(),
  }),
  effect: "write",
  dryRunResult: (input) => {
    const resolved = resolveBusSendInput({
      subject: input.subject,
      thread: input.thread,
      to: input.to,
    });
    if (!resolved.ok) {
      return { posted: false, error: resolved.error };
    }
    return {
      posted: false,
      thread: resolved.thread,
      subject: input.subject,
    };
  },
  async execute(input, ctx) {
    const resolvedFields = resolveBusSendInput({
      subject: input.subject,
      thread: input.thread,
      to: input.to,
      workspaceDir: ctx.workspaceDir,
    });
    if (!resolvedFields.ok) {
      return { posted: false, error: resolvedFields.error };
    }
    const resolved = {
      ...input,
      thread: resolvedFields.thread,
      to: resolvedFields.to,
    };
    if (isEvalOrFixture(ctx)) {
      return {
        posted: false,
        stubbed: true,
        from: IMPRESARIO_MAILBOX,
        ...resolved,
        recipient_corrected: resolvedFields.recipient_corrected,
        thread_defaulted: resolvedFields.thread_defaulted,
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
      recipient_corrected: resolvedFields.recipient_corrected,
      thread_defaulted: resolvedFields.thread_defaulted,
    };
  },
});
