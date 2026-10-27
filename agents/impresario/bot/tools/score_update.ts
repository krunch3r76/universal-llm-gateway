import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  checkScoreSizeAfterUpdate,
  resolveScoreUri,
} from "../lib/score-guards.js";
import { gateToolCall } from "../lib/tool-call-cap.js";
import { callAgentBus, callFs } from "../lib/ulg.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

const section = z.enum([
  "state",
  "dispatches",
  "open_stops",
  "rulings",
  "log",
]);

export default defineTool({
  description:
    "Rewrite one score section (state, dispatches, open_stops, rulings, log). Call after acting and on every designed stop (open_stops). Returns ok or size refusal. Idempotent; no stop.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    section,
    body: z.string().max(1500),
  }),
  effect: "idempotent",
  dryRunResult: ({ work_key, section: sec }) => ({
    ok: true,
    work_key,
    section: sec,
    dryRun: true,
  }),
  async execute(input, ctx) {
    return gateToolCall(ctx, async () => {
    const resolved = resolveScoreUri(input.work_key);
    if (!resolved.ok) {
      return resolved;
    }
    const path = resolved.uri;
    if (ctx.session.purpose === "eval") {
      return { ok: true, stubbed: true, ...input };
    }
    const existing = (await callFs(ctx, {
      op: "read",
      path,
      limit: 500,
    })) as { content?: string };
    const prior = existing.content ?? "";
    const sizeCheck = checkScoreSizeAfterUpdate(
      prior,
      input.section,
      input.body,
    );
    if (!sizeCheck.ok) {
      return sizeCheck;
    }
    await callFs(ctx, {
      op: "md_rewrite_section",
      path,
      section: input.section,
      content: input.body,
    });
    if (input.section === "state") {
      await callAgentBus(ctx, "substrate_graph_write", {
        entity_id: input.work_key,
        claim: input.body,
      });
    }
    return { ok: true, ...input };
    });
  },
});
