import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  authorFixPacketFromVerdict,
  filesExpectedSubsetOfCites,
} from "../lib/packet-author.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

export default defineTool({
  description:
    "Author a fix commission packet from a gc1 VERDICT| REQUEST_CHANGES body. files_expected are verdict file:line cites only; one acceptance criterion per review item plus the Before you post block. Read-only; no stop.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    verdict_body: z.string().max(8000),
  }),
  effect: "read",
  dryRunResult: (input) => ({
    ok: true,
    work_key: input.work_key,
    dryRun: true,
  }),
  execute(input) {
    const draft = authorFixPacketFromVerdict(input.verdict_body);
    const cites_ok = filesExpectedSubsetOfCites(
      draft.files_expected,
      input.verdict_body,
    );
    return {
      ok: true,
      work_key: input.work_key,
      scope: draft.scope,
      files_expected: draft.files_expected,
      acceptance: draft.acceptance,
      review_item_count: draft.review_items.length,
      cites_ok,
    };
  },
});
