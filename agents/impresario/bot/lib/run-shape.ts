import os from "node:os";
import path from "node:path";

import type { HostContext } from "@grok/agent-sdk";

import { hostBusSend } from "./bus-host.js";
import { recordGatedTurn } from "./usage-rollup.js";
import {
  IMPRESARIO_LANE,
  IMPRESARIO_MAILBOX,
  modelTurnsEnabled,
} from "./ulg.js";

const SEAT_REG_KV = "seat_reg:last_post_at";
const SEAT_REG_MIN_MS = 60_000;

export function impresarioStateRoot(): string {
  const fromEnv = process.env.ULG_IMPRESARIO_STATE_ROOT?.trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(os.homedir(), ".local/state/ulg-impresario");
}

export function seatRegistrationBody(stateRoot: string): string {
  const pid = process.pid;
  return [
    "TYPE: SEAT_REGISTRATION",
    `name = impresario-sdk · address = lane ${IMPRESARIO_LANE} · mailbox ${IMPRESARIO_MAILBOX} · host io pid ${pid} · state-root ${stateRoot} · sdk @grok/agent-sdk@0.3.0`,
  ].join("\n");
}

export async function maybePostSeatRegistration(
  host: HostContext,
  stateRoot: string,
): Promise<boolean> {
  const now = Date.now();
  const last = (await host.kv.get(SEAT_REG_KV)) as number | undefined;
  if (typeof last === "number" && now - last < SEAT_REG_MIN_MS) {
    return false;
  }
  await hostBusSend(host, {
    thread: IMPRESARIO_LANE,
    to: IMPRESARIO_MAILBOX,
    from: IMPRESARIO_MAILBOX,
    subject: "STATUS|seat-registration",
    body: seatRegistrationBody(stateRoot),
  });
  await host.kv.put(SEAT_REG_KV, now);
  return true;
}

export function turnAddressedToMailbox(turn: Record<string, unknown>): boolean {
  const to = String(turn.to ?? "");
  return to === IMPRESARIO_MAILBOX;
}

export async function ingestTurnGated(
  host: HostContext,
  thread: string,
  turnNumber: number,
  logger: Pick<Console, "info">,
): Promise<void> {
  logger.info(
    `[bus] model_turns=0 consumed ${thread}#${turnNumber} (no session, no bus reply)`,
  );
  await recordGatedTurn(host);
}

export function shouldStartModelTurn(): boolean {
  return modelTurnsEnabled();
}
