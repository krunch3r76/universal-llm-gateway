import {
  effectiveBusSendTo,
  type E4ToolCall,
} from "./e4-rule-check.js";

export const CLOSEOUT_REVIEW_RECIPIENT = "grok-bot-gc1";

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
  const parsed = parseToolOutput(call.output);
  if (typeof parsed?.thread === "string" && parsed.thread.trim() !== "") {
    return parsed.thread.trim();
  }
  const fromArgs = call.args?.thread;
  return typeof fromArgs === "string" ? fromArgs.trim() : "";
}

export type EffectiveBusSendGateResult =
  | { ok: true }
  | { ok: false; reason: string };

/** E1 closeout: REQUEST|review| must reach grok-bot-gc1 with a thread id. */
function effectiveBusSendSubject(call: E4ToolCall): string {
  const parsed = parseToolOutput(call.output);
  if (typeof parsed?.subject === "string") {
    return parsed.subject;
  }
  return typeof call.args?.subject === "string" ? call.args.subject : "";
}

export function checkEffectiveReviewBusSend(
  call: E4ToolCall,
): EffectiveBusSendGateResult {
  const subject = effectiveBusSendSubject(call);
  if (!/^REQUEST\|review\|todo:/.test(subject)) {
    return { ok: false, reason: `subject mismatch: ${subject || "(missing)"}` };
  }
  const to = effectiveBusSendTo(call);
  if (to !== CLOSEOUT_REVIEW_RECIPIENT) {
    return {
      ok: false,
      reason: `effective recipient ${to || "(missing)"}, expected ${CLOSEOUT_REVIEW_RECIPIENT}`,
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
