/** K109 E4 rule check for green DONE closeout runs (reads may vary). */

export type E4ToolCall = {
  toolName: string;
  args?: Record<string, unknown>;
  output?: unknown;
};

const REVIEW_RECIPIENT = "grok-bot-gc1";

export type E4RuleResult = { ok: true } | { ok: false; reason: string };

const WRITE_TOOLS = new Set([
  "bus_send",
  "score_update",
  "commission",
  "steer_conductor",
  "request_ruling",
]);

function busSendSubject(args: Record<string, unknown> | undefined): string {
  return typeof args?.subject === "string" ? args.subject : "";
}

function scoreSection(args: Record<string, unknown> | undefined): string {
  return typeof args?.section === "string" ? args.section : "";
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

export function busSendWasPosted(output: unknown): boolean {
  const parsed = parseToolOutput(output);
  if (!parsed) {
    return false;
  }
  if (parsed.error !== undefined && parsed.error !== null && parsed.error !== "") {
    return false;
  }
  return parsed.posted === true;
}

/** Effective recipient after a posted bus_send (output.to only). */
export function effectiveBusSendTo(call: E4ToolCall): string {
  if (!busSendWasPosted(call.output)) {
    return "";
  }
  const parsed = parseToolOutput(call.output);
  if (typeof parsed?.to === "string") {
    return parsed.to;
  }
  return "";
}

export function checkE4CloseoutRun(
  toolCalls: E4ToolCall[],
  workKey = "todo:reaper-amendment",
): E4RuleResult {
  const requestSubject = `REQUEST|review|${workKey}`;

  if (toolCalls.some((c) => c.toolName === "commission")) {
    return { ok: false, reason: "forbidden: commission" };
  }
  if (toolCalls.some((c) => c.toolName === "steer_conductor")) {
    return { ok: false, reason: "forbidden: steer_conductor on green closeout" };
  }

  const busSends = toolCalls.filter((c) => c.toolName === "bus_send");
  const postedReviewSends = busSends.filter((c) => busSendWasPosted(c.output));
  if (postedReviewSends.length !== 1) {
    return {
      ok: false,
      reason: `required exactly one posted bus_send, got ${postedReviewSends.length}`,
    };
  }
  const reviewSend = postedReviewSends[0]!;
  const subject = busSendSubject(reviewSend.args);
  if (subject !== requestSubject) {
    return {
      ok: false,
      reason: `bus_send subject must be ${requestSubject}, got ${subject || "(missing)"}`,
    };
  }
  const reviewTo = effectiveBusSendTo(reviewSend);
  if (reviewTo !== REVIEW_RECIPIENT) {
    return {
      ok: false,
      reason: `forbidden: REQUEST|review| bus_send to=${reviewTo || "(missing)"}`,
    };
  }

  const stateUpdates = toolCalls.filter(
    (c) => c.toolName === "score_update" && scoreSection(c.args) === "state",
  );
  if (stateUpdates.length < 1) {
    return { ok: false, reason: "missing required score_update section=state" };
  }

  for (const call of toolCalls) {
    if (!WRITE_TOOLS.has(call.toolName)) {
      continue;
    }
    if (call.toolName === "bus_send") {
      continue;
    }
    if (call.toolName === "score_update") {
      const section = scoreSection(call.args);
      if (section === "log") {
        continue;
      }
      if (section === "state") {
        continue;
      }
      return {
        ok: false,
        reason: `forbidden score_update section=${section || "(missing)"}`,
      };
    }
    return { ok: false, reason: `forbidden write tool ${call.toolName}` };
  }

  if (!toolCalls.some((c) => c.toolName === "closeout_read")) {
    return { ok: false, reason: "missing closeout_read" };
  }

  return { ok: true };
}
