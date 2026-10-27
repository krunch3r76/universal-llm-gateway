import type { HostContext } from "@grok/agent-sdk";

import { IMPRESARIO_LANE } from "./ulg.js";

export const LAST_LANE_TURN_KEY = "bus:last_lane_turn";

export async function readLaneCursor(host: HostContext): Promise<number> {
  const v = await host.kv.get(LAST_LANE_TURN_KEY);
  return typeof v === "number" ? v : 0;
}

export async function persistLaneCursor(
  host: HostContext,
  thread: string,
  turnNumber: number,
): Promise<void> {
  if (thread === IMPRESARIO_LANE) {
    await host.kv.put(LAST_LANE_TURN_KEY, turnNumber);
  }
}
