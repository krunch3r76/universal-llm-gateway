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
    "Read the executive closeout envelope (≤60 lines, stops before effects_manifest).",
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
