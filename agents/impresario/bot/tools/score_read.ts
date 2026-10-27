import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { callFs, scoreUri } from "../lib/ulg.js";

export default defineTool({
  description:
    "Read the impresario score for a work_key (≤4.5 KB). This is your only durable memory.",
  inputSchema: z.object({
    work_key: z.string().regex(/^(todo|friction):/),
  }),
  effect: "read",
  async execute({ work_key }, ctx) {
    const path = scoreUri(work_key);
    const limit = 200;
    const result = (await callFs(ctx, {
      op: "read",
      path,
      limit,
    })) as { content?: string };
    const content = result.content ?? "";
    if (content.length > 4500) {
      return { content: content.slice(0, 4500), truncated: true, path };
    }
    return { content, truncated: false, path };
  },
});
