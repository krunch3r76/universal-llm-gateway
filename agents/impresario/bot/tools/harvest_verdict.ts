import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { harvestVerdictToScore } from "../lib/score-memory.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

export default defineTool({
  description:
    "Harvest a gc1 VERDICT| line into score stage (verdict_at + verdict). Call on VERDICT| turns before commission or steer.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    verdict_line: z.string().max(2000),
  }),
  effect: "write",
  dryRunResult: (input) => ({
    ok: true,
    work_key: input.work_key,
    dryRun: true,
  }),
  async execute(input, ctx) {
    const harvest = await harvestVerdictToScore(
      ctx,
      input.work_key,
      input.verdict_line,
    );
    if (!harvest.ok) {
      return {
        ok: false,
        reason: harvest.reason,
        detail: harvest.detail,
      };
    }
    return {
      ok: true,
      work_key: input.work_key,
      harvested: harvest.harvested ?? true,
    };
  },
});
