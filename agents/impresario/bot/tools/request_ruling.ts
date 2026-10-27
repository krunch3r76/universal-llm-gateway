import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import {
  callAgentBus,
  IMPRESARIO_LANE,
  isEvalOrFixture,
  MAESTRO_THREAD,
  truncateBody,
} from "../lib/ulg.js";
import { WORK_KEY_ZOD_REGEX } from "../lib/work-key.js";

export default defineTool({
  description:
    "Escalate an unresolved worker question to Kaywan via maestro. Posts identical QUESTION turns on the impresario lane and thread 12286. Always returns stop OPERATOR_GATE; resume when a FACT arrives on 12286.",
  inputSchema: z.object({
    work_key: z.string().regex(WORK_KEY_ZOD_REGEX),
    question: z.string().max(600),
    recommended_default: z.string().max(300),
  }),
  effect: "write",
  dryRunResult: ({ work_key }) => ({
    ok: false,
    stop: "OPERATOR_GATE",
    work_key,
    posted: false,
  }),
  async execute(input, ctx) {
    const subject = `QUESTION|${input.work_key}`;
    const body = truncateBody(
      `${input.question}\n\nrecommended_default: ${input.recommended_default}`,
      2000,
    );

    const sendOne = async (thread: string) => {
      if (isEvalOrFixture(ctx)) {
        return callAgentBus(ctx, "send", {
          thread,
          to: "maestro",
          subject,
          body,
        });
      }
      return callAgentBus(ctx, "send", {
        thread,
        to: "maestro",
        subject,
        body,
      });
    };

    await sendOne(IMPRESARIO_LANE);
    await sendOne(MAESTRO_THREAD);

    return {
      ok: false,
      stop: "OPERATOR_GATE" as const,
      resume_on: "FACT| from Kaywan on 12286",
      work_key: input.work_key,
      posted: !isEvalOrFixture(ctx),
    };
  },
});
