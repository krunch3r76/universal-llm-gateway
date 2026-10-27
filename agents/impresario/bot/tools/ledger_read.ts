import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { readLedger } from "../lib/dispatch.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

export default defineTool({
  description:
    "Read the team_dispatch ledger rows for one work_key (op=ledger projection). Call before commission to detect ROW_PINNED. Returns a compact JSON summary under 2500 characters.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
  }),
  effect: "read",
  async execute({ work_key }, ctx) {
    const { rows, json } = await readLedger(ctx, work_key);
    return {
      work_key,
      row_count: rows.length,
      projection: json,
      chars: json.length,
    };
  },
});
