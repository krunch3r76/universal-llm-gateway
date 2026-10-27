import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { callAgentBus, callFs, isEvalOrFixture, scoreUri } from "../lib/ulg.js";

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
    if (isEvalOrFixture(ctx)) {
      return { ok: true, stubbed: true, ...input };
    }
    const path = scoreUri(input.work_key);
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
