import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  resolveScoreUri,
  SCORE_READ_LINE_LIMIT,
  windowScoreContent,
} from "../lib/score-guards.js";
import { callFs } from "../lib/ulg.js";

export default defineTool({
  description:
    "Read the impresario score for a work_key (≤4.5 KB). This is your only durable memory.",
  inputSchema: z.object({
    work_key: z.string().regex(/^(todo|friction):/),
  }),
  effect: "read",
  async execute({ work_key }, ctx) {
    const resolved = resolveScoreUri(work_key);
    if (!resolved.ok) {
      return resolved;
    }
    const path = resolved.uri;
    const result = (await callFs(ctx, {
      op: "read",
      path,
      limit: SCORE_READ_LINE_LIMIT,
    })) as { content?: string };
    const raw = result.content ?? "";
    const { content, truncated } = windowScoreContent(
      raw,
      SCORE_READ_LINE_LIMIT,
    );
    return { content, truncated, path };
  },
});
