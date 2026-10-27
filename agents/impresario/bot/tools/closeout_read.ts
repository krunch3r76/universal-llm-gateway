import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { closeoutFromFixture, fixturesEnabled } from "../lib/bus-fixtures.js";
import {
  callFs,
  closeoutUri,
  parseCloseoutExcerpt,
} from "../lib/ulg.js";

export default defineTool({
  description:
    "Read one closeout executive envelope (≤60 lines, before effects_manifest). Call once per DONE/CLOSEOUT turn. Returns verdict_line and residue. Read-only; no stop.",
  inputSchema: z.object({
    dispatch_id: z
      .string()
      .min(8)
      .regex(/^[0-9a-f-]+$/i),
  }),
  effect: "read",
  async execute({ dispatch_id }, ctx) {
    if (fixturesEnabled()) {
      return closeoutFromFixture(dispatch_id);
    }
    const path = closeoutUri(dispatch_id);
    await callFs(ctx, { op: "md_list", path });
    const read = (await callFs(ctx, {
      op: "read",
      path,
      limit: 60,
    })) as { content?: string };
    return parseCloseoutExcerpt(read.content ?? "");
  },
});
