import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { closeoutFromFixture, fixturesEnabled } from "../lib/bus-fixtures.js";
import { harvestCloseoutToScore } from "../lib/score-memory.js";
import {
  callFs,
  closeoutUri,
  parseCloseoutExcerpt,
} from "../lib/ulg.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

export default defineTool({
  description:
    "Read one closeout executive envelope (≤60 lines, before effects_manifest). Call once per DONE/CLOSEOUT turn. Returns verdict_line and residue. When work_key is set, harvests stage.done_at and worker terminal state into the score.",
  inputSchema: z.object({
    dispatch_id: z
      .string()
      .min(8)
      .regex(/^[0-9a-f-]+$/i),
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX).optional(),
  }),
  effect: "read",
  async execute({ dispatch_id, work_key }, ctx) {
    let excerpt;
    if (fixturesEnabled()) {
      excerpt = closeoutFromFixture(dispatch_id);
    } else {
      const path = closeoutUri(dispatch_id);
      await callFs(ctx, { op: "md_list", path });
      const read = (await callFs(ctx, {
        op: "read",
        path,
        limit: 60,
      })) as { content?: string };
      excerpt = parseCloseoutExcerpt(read.content ?? "");
    }

    if (work_key) {
      const harvest = await harvestCloseoutToScore(ctx, work_key, dispatch_id);
      return {
        ...excerpt,
        score_harvest: harvest.ok,
        ...(harvest.ok
          ? {}
          : { score_harvest_reason: harvest.reason, score_harvest_detail: harvest.detail }),
      };
    }
    return excerpt;
  },
});
