import { resolveImpresarioReviewMailbox } from "../../bot/lib/ulg.js";
import {
  isReviewRequestSubject,
  parseSubjectTokens,
  reviewRequestRecipient,
} from "../../bot/tools/bus_send.js";
import {
  busSendWasPosted,
  effectiveBusSendTo,
  type E4ToolCall,
} from "./e4-rule-check.js";

export function closeoutReviewRecipient(): string {
  return reviewRequestRecipient();
}

function parseToolOutput(output: unknown): Record<string, unknown> | null {
  if (output === undefined || output === null) {
    return null;
  }
  if (typeof output === "object" && !Array.isArray(output)) {
    return output as Record<string, unknown>;
  }
  if (typeof output === "string") {
    try {
      return JSON.parse(output) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
  return null;
}

/** Thread actually sent (tool result wins over model args). */
export function effectiveBusSendThread(call: E4ToolCall): string {
  if (!busSendWasPosted(call.output)) {
    return "";
  }
  const parsed = parseToolOutput(call.output);
  if (typeof parsed?.thread === "string" && parsed.thread.trim() !== "") {
    return parsed.thread.trim();
  }
  return "";
}

export type EffectiveBusSendGateResult =
  | { ok: true }
  | { ok: false; reason: string };

/** E1 closeout: REQUEST|review| must reach grok-bot-gc1 with a thread id. */
function effectiveBusSendSubject(call: E4ToolCall): string {
  if (!busSendWasPosted(call.output)) {
    return "";
  }
  const parsed = parseToolOutput(call.output);
  if (typeof parsed?.subject === "string") {
    return parsed.subject;
  }
  return typeof call.args?.subject === "string" ? call.args.subject : "";
}

export function checkEffectiveReviewBusSend(
  call: E4ToolCall,
): EffectiveBusSendGateResult {
  if (!busSendWasPosted(call.output)) {
    return { ok: false, reason: "bus_send was not posted" };
  }
  const subject = effectiveBusSendSubject(call);
  if (!isReviewRequestSubject(subject)) {
    return { ok: false, reason: `subject mismatch: ${subject || "(missing)"}` };
  }
  const workKey = parseSubjectTokens(subject)[2] ?? "";
  if (!/^todo:/i.test(workKey)) {
    return { ok: false, reason: `subject work_key mismatch: ${workKey || "(missing)"}` };
  }
  const to = effectiveBusSendTo(call);
  const expectedReview = resolveImpresarioReviewMailbox();
  if (!expectedReview.ok) {
    return { ok: false, reason: expectedReview.error };
  }
  if (to !== expectedReview.mailbox) {
    return {
      ok: false,
      reason: `effective recipient ${to || "(missing)"}, expected ${expectedReview.mailbox}`,
    };
  }
  const thread = effectiveBusSendThread(call);
  if (!thread) {
    return { ok: false, reason: "effective thread missing" };
  }
  return { ok: true };
}

/** Eval `calledTool` output matcher (checks tool result, not model input.to). */
export function reviewBusSendOutputMatches(value: unknown): boolean {
  return checkEffectiveReviewBusSend({
    toolName: "bus_send",
    args: {},
    output: value,
  }).ok;
}
