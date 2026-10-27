import type { HostContext } from "@grok/agent-sdk";

import { IMPRESARIO_LANE } from "./ulg.js";

export const LAST_LANE_TURN_KEY = "bus:last_lane_turn";

const laneCursorLogger = console;

export async function readLaneCursor(host: HostContext): Promise<number> {
  try {
    const v = await host.kv.get(LAST_LANE_TURN_KEY);
    return typeof v === "number" ? v : 0;
  } catch (err) {
    laneCursorLogger.warn("[bus-lane] kv.get failed; using cursor 0", err);
    return 0;
  }
}

export async function persistLaneCursor(
  host: HostContext,
  thread: string,
  turnNumber: number,
): Promise<void> {
  if (thread === IMPRESARIO_LANE) {
    try {
      await host.kv.put(LAST_LANE_TURN_KEY, turnNumber);
    } catch (err) {
      laneCursorLogger.warn(
        "[bus-lane] kv.put failed; lane cursor may be stale",
        err,
      );
    }
  }
}
