import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  checkScoreSizeAfterUpdate,
  resolveScoreUri,
} from "../lib/score-guards.js";
import { callAgentBus, callFs } from "../lib/ulg.js";

const section = z.enum([
  "state",
  "dispatches",
  "open_stops",
  "rulings",
  "log",
]);

export default defineTool({
  description:
    "Append or rewrite one score section. State updates also assert on the todo entity.",
  inputSchema: z.object({
    work_key: z.string().regex(/^(todo|friction):/),
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
  },
});
