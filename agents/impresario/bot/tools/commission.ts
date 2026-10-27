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
  formatStageSection,
  formatWorkerSection,
  parseStageSection,
  resolveResumeFromScore,
  stageAfterCommission,
} from "../lib/score-memory.js";
import { readScoreMarkdown } from "../lib/score.js";
import { callFs, resolveScoreUri } from "../lib/ulg.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

const contractEnum = z.enum(["freeform", "conductor"]);

async function persistCommissionScoreMemory(
  ctx: Parameters<typeof callFs>[0],
  workKey: string,
  worker: {
    dispatch_id: string;
    thread_id: string;
    execution_id: string;
    state: string;
  },
): Promise<void> {
  const resolved = resolveScoreUri(workKey);
  if (!resolved.ok || ctx.session.purpose === "eval") {
    return;
  }
  const path = resolved.uri;
  const existing = (await callFs(ctx, {
    op: "read",
    path,
    limit: 500,
  })) as { content?: string };
  const prior = existing.content ?? "";
  const now = new Date().toISOString();
  const stage = stageAfterCommission(parseStageSection(prior), now);
  await callFs(ctx, {
    op: "md_rewrite_section",
    path,
    section: "worker",
    content: formatWorkerSection(worker),
  });
  await callFs(ctx, {
    op: "md_rewrite_section",
    path,
    section: "stage",
    content: formatStageSection(stage),
  });
}

export default defineTool({
  description:
    "Commission a cursor-sdk lane-B worker for a work_key. Call after ledger_read when starting new dispatch work. Returns dry-run dispatch metadata when live steer is off, or dispatch ids when live. May return stop ROW_PINNED when the ledger shows an in-flight row for the same work_key. When the score worker is terminal and stage verdict is REQUEST_CHANGES, passes resume_of and reuse_thread automatically.",
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
    let resume_of = input.resume_of;
    let reuse_thread = input.reuse_thread;
    const resume = resolveResumeFromScore(scoreText);
    if (resume.ok) {
      if (!resume_of) {
        resume_of = resume.dispatch_id;
      }
      if (!reuse_thread) {
        reuse_thread = resume.thread_id;
      }
    }

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
      let dispatched: Record<string, unknown> | undefined;
      if (process.env.ULG_FIXTURE_DIR) {
        dispatched = (await commissionGenerate(ctx, generateArgs)) as Record<
          string,
          unknown
        >;
        if (dispatched?.dispatch_id) {
          await persistCommissionScoreMemory(ctx, input.work_key, {
            dispatch_id: String(dispatched.dispatch_id),
            thread_id: String(
              dispatched.thread_id ?? reuse_thread ?? "worker-fixture",
            ),
            execution_id: String(dispatched.execution_id ?? ""),
            state: "running",
          });
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
    if (dispatch_id) {
      await persistCommissionScoreMemory(ctx, input.work_key, {
        dispatch_id,
        thread_id: String(dispatched.thread_id ?? reuse_thread ?? ""),
        execution_id: String(dispatched.execution_id ?? ""),
        state: "running",
      });
    }
    return {
      ok: true,
      posted: true,
      rendered_prompt_sha256,
      dispatch_id,
      resume_of: resume_of ?? null,
      reuse_thread: reuse_thread ?? null,
    };
  },
});
