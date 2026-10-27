import fs from "node:fs";
import path from "node:path";

import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { gateToolCall } from "../lib/tool-call-cap.js";
import {
  busMailbox,
  IMPRESARIO_MAILBOX,
  callAgentBus,
  isEvalOrFixture,
} from "../lib/ulg.js";

export const REVIEW_REQUEST_RECIPIENT = "grok-bot-gc1";

/** Threads allowed when turn.json is missing or has no thread (tests / explicit pins). */
export const ALLOWLISTED_BUS_THREADS = new Set<string>();

export type TurnJsonReadResult =
  | { ok: true; thread: string | null }
  | { ok: false; error: string };

/** Read `thread` from seeded session workspace turn.json when present. */
export function readTurnJsonThread(workspaceDir: string): TurnJsonReadResult {
  const turnPath = path.join(workspaceDir, "turn.json");
  let raw: string;
  try {
    raw = fs.readFileSync(turnPath, "utf8");
  } catch {
    return { ok: true, thread: null };
  }
  try {
    const turn = JSON.parse(raw) as { thread?: unknown };
    if (turn.thread === undefined || turn.thread === null) {
      return { ok: true, thread: null };
    }
    if (typeof turn.thread !== "string" || turn.thread.trim() === "") {
      return { ok: false, error: "turn.json thread must be a non-empty string" };
    }
    return { ok: true, thread: turn.thread.trim() };
  } catch {
    return { ok: false, error: "turn.json is not valid JSON" };
  }
}

/** Pipe-separated subject tokens (trimmed, empty segments dropped). */
export function parseSubjectTokens(subject: string): string[] {
  return subject
    .split("|")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

export function isReviewRequestSubject(subject: string): boolean {
  const tokens = parseSubjectTokens(subject);
  return (
    tokens.length >= 2 &&
    tokens[0]!.toUpperCase() === "REQUEST" &&
    tokens[1]!.toUpperCase() === "REVIEW"
  );
}

export type BusSendAckResult =
  | { ok: true; turn_number?: number; turn_id?: number | string }
  | { ok: false; error: string };

/** True when agent_bus send payload confirms a posted turn. */
export function busSendResponsePosted(body: unknown): BusSendAckResult {
  if (body === null || body === undefined || typeof body !== "object") {
    return { ok: false, error: "bus send response missing turn confirmation" };
  }
  const record = body as Record<string, unknown>;
  const turn = record.turn;
  if (turn !== null && turn !== undefined && typeof turn === "object") {
    const turnRec = turn as Record<string, unknown>;
    if (turnRec.id !== undefined && turnRec.id !== null) {
      return {
        ok: true,
        turn_id: turnRec.id as number | string,
        turn_number:
          typeof turnRec.turn_number === "number"
            ? turnRec.turn_number
            : undefined,
      };
    }
    if (typeof turnRec.turn_number === "number") {
      return { ok: true, turn_number: turnRec.turn_number };
    }
  }
  if (typeof record.turn_number === "number") {
    return { ok: true, turn_number: record.turn_number };
  }
  if (record.id !== undefined && record.id !== null) {
    return { ok: true, turn_id: record.id as number | string };
  }
  return { ok: false, error: "bus send response missing turn confirmation" };
}

export type BusSendDryRunPayload = {
  posted: boolean;
  error?: string;
  thread?: string;
  subject?: string;
  workspaceDir: string;
};

export function busSendDryRunResult(
  input: { subject: string; thread?: string; to?: string },
  workspaceDir: string,
): BusSendDryRunPayload {
  const resolved = resolveBusSendInput({
    subject: input.subject,
    thread: input.thread,
    to: input.to,
    workspaceDir,
  });
  if (!resolved.ok) {
    return { posted: false, error: resolved.error, workspaceDir };
  }
  return {
    posted: false,
    thread: resolved.thread,
    subject: input.subject,
    workspaceDir,
  };
}

/** REQUEST|review| always posts to the stage manager mailbox. */
export function resolveReviewRequestRecipient(
  subject: string,
  to: string | undefined,
): { to: string; recipient_corrected: boolean } {
  if (isReviewRequestSubject(subject)) {
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
      thread_pinned: boolean;
    }
  | { ok: false; error: string };

function resolveThreadForSend(input: {
  subject: string;
  thread?: string;
  workspaceDir?: string;
}): ResolveBusSendResult | { ok: false; error: string } {
  const requested = input.thread?.trim();
  let turnThread: string | null = null;
  if (input.workspaceDir !== undefined) {
    const fromTurn = readTurnJsonThread(input.workspaceDir);
    if (!fromTurn.ok) {
      return fromTurn;
    }
    turnThread = fromTurn.thread;
  }

  const review = isReviewRequestSubject(input.subject);

  if (review) {
    if (turnThread) {
      return {
        ok: true,
        thread: turnThread,
        to: "",
        recipient_corrected: false,
        thread_defaulted: !requested,
        thread_pinned: Boolean(requested && requested !== turnThread),
      };
    }
    if (requested && ALLOWLISTED_BUS_THREADS.has(requested)) {
      return {
        ok: true,
        thread: requested,
        to: "",
        recipient_corrected: false,
        thread_defaulted: false,
        thread_pinned: false,
      };
    }
    return {
      ok: false,
      error: "thread is required when turn.json is missing or has no thread",
    };
  }

  if (!requested) {
    if (turnThread) {
      return {
        ok: true,
        thread: turnThread,
        to: "",
        recipient_corrected: false,
        thread_defaulted: true,
        thread_pinned: false,
      };
    }
    return {
      ok: false,
      error: "thread is required when turn.json is missing or has no thread",
    };
  }

  if (turnThread !== null && requested !== turnThread) {
    return {
      ok: false,
      error: `thread ${requested} does not match turn.json thread ${turnThread}`,
    };
  }
  if (turnThread === null && !ALLOWLISTED_BUS_THREADS.has(requested)) {
    return {
      ok: false,
      error: `thread ${requested} is not allowed without turn.json`,
    };
  }

  return {
    ok: true,
    thread: requested,
    to: "",
    recipient_corrected: false,
    thread_defaulted: false,
    thread_pinned: false,
  };
}

export function resolveBusSendInput(input: {
  subject: string;
  thread?: string;
  to?: string;
  workspaceDir?: string;
}): ResolveBusSendResult {
  const threadPart = resolveThreadForSend(input);
  if (!threadPart.ok) {
    return threadPart;
  }
  const {
    thread,
    thread_defaulted,
    thread_pinned,
  } = threadPart;

  if (isReviewRequestSubject(input.subject)) {
    const { to, recipient_corrected } = resolveReviewRequestRecipient(
      input.subject,
      input.to,
    );
    return {
      ok: true,
      thread,
      to,
      recipient_corrected,
      thread_defaulted,
      thread_pinned,
    };
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
    thread_pinned,
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
      .regex(/^(DONE|FAILED|REQUEST|QUESTION|STATUS|ACK|STOP)\|/i),
    body: z.string().max(2000),
    sidecar_content: z.string().max(256_000).optional(),
  }),
  effect: "write",
  dryRunResult: (input) => {
    const workspaceDir =
      process.env.ULG_IMPRESARIO_DRY_RUN_WORKSPACE?.trim() ?? "";
    return busSendDryRunResult(input, workspaceDir);
  },
  async execute(input, ctx) {
    return gateToolCall(ctx, async () => {
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
          posted: true,
          stubbed: true,
          from: IMPRESARIO_MAILBOX,
          ...resolved,
          recipient_corrected: resolvedFields.recipient_corrected,
          thread_defaulted: resolvedFields.thread_defaulted,
          thread_pinned: resolvedFields.thread_pinned,
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
      const busBody = await callAgentBus(ctx, "send", payload);
      const ack = busSendResponsePosted(busBody);
      if (!ack.ok) {
        return { posted: false, error: ack.error };
      }
      return {
        posted: true,
        thread: resolved.thread,
        subject: resolved.subject,
        to: resolved.to,
        recipient_corrected: resolvedFields.recipient_corrected,
        thread_defaulted: resolvedFields.thread_defaulted,
        thread_pinned: resolvedFields.thread_pinned,
        turn_number: ack.turn_number,
        turn_id: ack.turn_id,
      };
    });
  },
});
