import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  findDispatchRow,
  isConsultParked,
  resumeOnConsultPending,
  resumeOnNotFound,
  shouldLiveDispatch,
  steerInject,
} from "../lib/dispatch.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

export default defineTool({
  description:
    "Inject a directive into a live conductor dispatch. Call when a verdict or ruling requires steering an admitted conductor. Returns the 202 inject ticket when accepted. May return stop CONSULT_PENDING when the dispatch is consult-parked.",
  inputSchema: z.object({
    dispatch_id: z.string().min(8),
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    directive: z.string().max(1200),
    reason: z.string().max(200),
  }),
  effect: "write",
  dryRunResult: ({ dispatch_id }) => ({
    would_steer: true,
    dispatch_id,
    posted: false,
  }),
  async execute(input, ctx) {
    const row = await findDispatchRow(ctx, input.dispatch_id, input.work_key);

    if (!row) {
      return {
        ok: false,
        stop: "NOT_FOUND" as const,
        dispatch_id: input.dispatch_id,
        resume_on: resumeOnNotFound(input.dispatch_id, input.work_key),
      };
    }

    if (isConsultParked(row)) {
      return {
        ok: false,
        stop: "CONSULT_PENDING" as const,
        dispatch_id: input.dispatch_id,
        resume_on: resumeOnConsultPending(row),
      };
    }

    if (!shouldLiveDispatch(ctx)) {
      return {
        would_steer: true,
        posted: false,
        dispatch_id: input.dispatch_id,
      };
    }

    const ticket = (await steerInject(ctx, input)) as { inject_state?: string };
    const inject_state = String(ticket.inject_state ?? "pending").slice(0, 500);
    return { ok: true, inject_state, posted: true };
  },
});
