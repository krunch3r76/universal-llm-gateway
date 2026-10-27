import { defineHook } from "@grok/agent-sdk/hooks";

import { hostBusSend } from "../lib/bus-host.js";
import { IMPRESARIO_LANE, MAESTRO_THREAD } from "../lib/ulg.js";

export async function mirrorTurnFailed(
  host: Parameters<typeof hostBusSend>[0],
  sessionId: string,
  message: string,
): Promise<void> {
  const subject = `FAILED|impresario|${sessionId}`;
  const body = message.slice(0, 2000);
  const payload = {
    thread: IMPRESARIO_LANE,
    to: "maestro",
    from: "impresario-sdk",
    subject,
    body,
  };
  await hostBusSend(host, { ...payload, thread: IMPRESARIO_LANE });
  await hostBusSend(host, { ...payload, thread: MAESTRO_THREAD });
}

export default defineHook({
  events: {
    async "turn.failed"(event, ctx) {
      if (ctx.session.purpose === "eval") {
        return;
      }
      const status = event.data.status;
      if (status === "cancelled") {
        return;
      }
      await mirrorTurnFailed(
        ctx.host,
        ctx.session.id,
        event.data.message ?? "turn failed",
      );
    },
  },
});
