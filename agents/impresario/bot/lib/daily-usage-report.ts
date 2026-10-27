import type { HostContext } from "@grok/agent-sdk";

import { hostBusSend } from "./bus-host.js";
import {
  formatUsageRollup,
  readUsageRollup,
} from "./usage-rollup.js";
import { IMPRESARIO_LANE } from "./ulg.js";

export async function postDailyUsageStatus(host: HostContext): Promise<void> {
  const rollup = await readUsageRollup(host);
  const summary = formatUsageRollup(rollup);
  await hostBusSend(host, {
    thread: IMPRESARIO_LANE,
    to: "maestro",
    from: "impresario-sdk",
    subject: "STATUS|usage|impresario",
    body: summary,
  });
}
