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
import {
  persistCommissionScoreMemory,
  validateCommissionResumeFields,
} from "../lib/score-memory.js";
import { readScoreMarkdown } from "../lib/score.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

const contractEnum = z.enum(["freeform", "conductor"]);

export default defineTool({
  description:
    "Commission a cursor-sdk lane-B worker for a work_key. Call after ledger_read when starting new dispatch work. Returns dry-run dispatch metadata when live steer is off, or dispatch ids when live. May return stop ROW_PINNED when the ledger shows an in-flight row for the same work_key. When the score worker is terminal and stage verdict is REQUEST_CHANGES, passes resume_of and reuse_thread automatically. Rejects nest_under with resume_of and resume_of without reuse_thread.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    contract: contractEnum,
    scope: z.string().max(400),
    files_expected: z.array(z.string()).max(20),
    acceptance: z.array(z.string()).max(20),
    nest_under: z.string().optional(),
    resume_of: z.string().optional(),
    reuse_thread: z.string().optional(),
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

    const scoreText = await readScoreMarkdown(ctx.host, input.work_key, "");
    const resumeCheck = validateCommissionResumeFields({
      nest_under: input.nest_under,
      resume_of: input.resume_of,
      reuse_thread: input.reuse_thread,
      scoreText,
    });
    if (!resumeCheck.ok) {
      return { ok: false, reason: resumeCheck.reason };
    }
    const resume_of = resumeCheck.resume_of;
    const reuse_thread = resumeCheck.reuse_thread;

    const prompt = renderCommissionPrompt(input);
    const rendered_prompt_sha256 = promptSha256(prompt);

    const generateArgs = {
      work_key: input.work_key,
      contract: input.contract,
      prompt,
      nest_under: input.nest_under,
      resume_of,
      reuse_thread,
    };

    if (!shouldLiveDispatch(ctx)) {
      if (process.env.ULG_FIXTURE_DIR) {
        const dispatched = (await commissionGenerate(ctx, generateArgs)) as Record<
          string,
          unknown
        >;
        if (dispatched?.dispatch_id) {
          const dispatch_id = String(dispatched.dispatch_id);
          const persist = await persistCommissionScoreMemory(ctx, input.work_key, {
            dispatch_id,
            thread_id: String(
              dispatched.thread_id ?? reuse_thread ?? "worker-fixture",
            ),
            execution_id: String(dispatched.execution_id ?? ""),
            state: "running",
          });
          if (!persist.ok) {
            return {
              would_dispatch: true,
              rendered_prompt_sha256,
              posted: false,
              dispatch_id,
              score_persist_ok: false,
              score_persist_reason: persist.reason,
              resume_of: resume_of ?? null,
              reuse_thread: reuse_thread ?? null,
            };
          }
        }
      }
      return {
        would_dispatch: true,
        rendered_prompt_sha256,
        posted: false,
        resume_of: resume_of ?? null,
        reuse_thread: reuse_thread ?? null,
      };
    }

    const dispatched = (await commissionGenerate(ctx, generateArgs)) as Record<
      string,
      unknown
    >;
    const dispatch_id = String(dispatched.dispatch_id ?? "");
    if (!dispatch_id) {
      return {
        ok: false,
        reason: "dispatch_id_missing",
        rendered_prompt_sha256,
      };
    }

    const workerPatch = {
      dispatch_id,
      thread_id: String(dispatched.thread_id ?? reuse_thread ?? ""),
      execution_id: String(dispatched.execution_id ?? ""),
      state: "running",
    };
    let persist;
    try {
      persist = await persistCommissionScoreMemory(ctx, input.work_key, workerPatch);
    } catch (err) {
      return {
        ok: true,
        posted: true,
        rendered_prompt_sha256,
        dispatch_id,
        score_persist_ok: false,
        score_persist_reason: "score_write_failed",
        score_persist_detail: err instanceof Error ? err.message : String(err),
        resume_of: resume_of ?? null,
        reuse_thread: reuse_thread ?? null,
      };
    }
    if (!persist.ok) {
      return {
        ok: true,
        posted: true,
        rendered_prompt_sha256,
        dispatch_id,
        score_persist_ok: false,
        score_persist_reason: persist.reason,
        score_persist_detail: persist.detail,
        resume_of: resume_of ?? null,
        reuse_thread: reuse_thread ?? null,
      };
    }

    return {
      ok: true,
      posted: true,
      rendered_prompt_sha256,
      dispatch_id,
      score_persist_ok: true,
      resume_of: resume_of ?? null,
      reuse_thread: reuse_thread ?? null,
    };
  },
});
