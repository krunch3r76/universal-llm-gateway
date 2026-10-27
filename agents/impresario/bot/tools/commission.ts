import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  commissionGenerate,
  findInFlightRow,
  promptSha256,
  readLedger,
  renderCommissionPrompt,
  resumeOnRowPinned,
  shouldLiveDispatch,
} from "../lib/dispatch.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

const contractEnum = z.enum(["freeform", "conductor"]);

export default defineTool({
  description:
    "Commission a cursor-sdk lane-B worker for a work_key. Call after ledger_read when starting new dispatch work. Returns dry-run dispatch metadata when live steer is off, or dispatch ids when live. May return stop ROW_PINNED when the ledger shows an in-flight row for the same work_key.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    contract: contractEnum,
    scope: z.string().max(400),
    files_expected: z.array(z.string()).max(20),
    acceptance: z.array(z.string()).max(20),
    nest_under: z.string().optional(),
    resume_of: z.string().optional(),
  }),
  effect: "write",
  dryRunResult: (input) => ({
    would_dispatch: true,
    posted: false,
    work_key: input.work_key,
  }),
  async execute(input, ctx) {
    const { rows } = await readLedger(ctx, input.work_key);
    const inflight = findInFlightRow(rows);
    if (inflight) {
      return {
        ok: false,
        stop: "ROW_PINNED" as const,
        dispatch_id: inflight.dispatch_id,
        resume_on: resumeOnRowPinned(inflight),
      };
    }

    const prompt = renderCommissionPrompt(input);
    const rendered_prompt_sha256 = promptSha256(prompt);

    if (!shouldLiveDispatch(ctx)) {
      if (process.env.ULG_FIXTURE_DIR) {
        await commissionGenerate(ctx, {
          work_key: input.work_key,
          contract: input.contract,
          prompt,
          nest_under: input.nest_under,
          resume_of: input.resume_of,
        });
      }
      return {
        would_dispatch: true,
        rendered_prompt_sha256,
        posted: false,
      };
    }

    const dispatched = (await commissionGenerate(ctx, {
      work_key: input.work_key,
      contract: input.contract,
      prompt,
      nest_under: input.nest_under,
      resume_of: input.resume_of,
    })) as Record<string, unknown>;
    return {
      ok: true,
      posted: true,
      rendered_prompt_sha256,
      dispatch_id: String(dispatched.dispatch_id ?? ""),
    };
  },
});
