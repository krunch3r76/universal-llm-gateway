import type { HostContext } from "@grok/agent-sdk";

import { hostBusSend } from "./bus-host.js";
import {
  formatUsageRollup,
  readUsageRollup,
} from "./usage-rollup.js";
import { getImpresarioLane } from "./ulg.js";

export async function postDailyUsageStatus(host: HostContext): Promise<void> {
  const rollup = await readUsageRollup(host);
  const summary = formatUsageRollup(rollup);
  await hostBusSend(host, {
    thread: getImpresarioLane(),
    to: "maestro",
    from: "impresario-sdk",
    subject: "STATUS|usage|impresario",
    body: summary,
  });
}
