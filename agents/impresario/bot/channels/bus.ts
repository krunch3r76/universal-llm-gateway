import {
  bearerAuth,
  defineChannel,
  POST,
} from "@grok/agent-sdk/channels";
import type { ChannelStartArgs, SendMessageFn } from "@grok/agent-sdk/channels";
import type { HostContext } from "@grok/agent-sdk";
import type { ToolContext } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { hostBusCall } from "../lib/bus-host.js";
import {
  fixtureBusOp,
  fixturesEnabled,
  loadScenario,
} from "../lib/bus-fixtures.js";
import {
  bumpThreadTurnCount,
  continuationToken,
  getThreadRotation,
  readScoreMarkdown,
  turnToJson,
  workKeyFromTurn,
} from "../lib/score.js";
import {
  impresarioStateRoot,
  ingestTurnGated,
  maybePostSeatRegistration,
  shouldStartModelTurn,
  turnAddressedToMailbox,
} from "../lib/run-shape.js";
import {
  agentSdkBearerForAuth,
  requireAgentSdkBearerToken,
} from "../lib/bearer.js";
import {
  bootstrapImpresarioRegistry,
  getImpresarioLane,
  IMPRESARIO_MAILBOX,
} from "../lib/ulg.js";
import {
  resolveUlgMcpHttpUrl,
  ulgMcpResolvedHost,
} from "../lib/ulg-mcp-url.js";

import { persistLaneCursor, readLaneCursor } from "../lib/bus-lane.js";

function busTransportLogMeta(phase: string, err: unknown): string {
  const configured = ulgMcpResolvedHost(resolveUlgMcpHttpUrl());
  const e = err as { code?: string; hostname?: string; host?: string };
  const errHost = e.hostname ?? e.host ?? "?";
  return `${new Date().toISOString()} configured_host=${configured} err_host=${errHost} phase=${phase} code=${e.code ?? "?"}`;
}

const DELIVERED_PREFIX = "delivered:";

async function busCall(
  host: HostContext,
  tool: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureBusOp(tool, payload);
  }
  return hostBusCall(host, tool, payload);
}

async function busWait(
  host: HostContext,
  lane: string,
  afterTurn: number,
  waitSeconds = 60,
): Promise<unknown> {
  return busCall(host, "wait", {
    thread: lane,
    after_turn: afterTurn,
    wait_seconds: waitSeconds,
  });
}

async function busFetchUnread(
  host: HostContext,
  mailbox: string,
  lane: string,
  compact = true,
): Promise<{ turns?: Record<string, unknown>[] }> {
  return busCall(host, "fetch_unread", {
    to: mailbox,
    thread: lane,
    compact,
  }) as Promise<{ turns?: Record<string, unknown>[] }>;
}

async function busGetTurn(
  host: HostContext,
  thread: string,
  turnNumber: number,
  markRead: boolean,
): Promise<{ turn?: Record<string, unknown> }> {
  return busCall(host, "get", {
    thread,
    turn_number: turnNumber,
    mark_read: markRead,
    projection: "compact",
  }) as Promise<{ turn?: Record<string, unknown> }>;
}

/** Per-recipient read_at for turns addressed `to=impresario-sdk`. */
async function busMarkReadForMailbox(
  host: HostContext,
  thread: string,
  turnNumber: number,
): Promise<void> {
  await busCall(host, "mark_read", {
    thread,
    through_turn: turnNumber,
    agent: IMPRESARIO_MAILBOX,
  });
}

let pollStopped = false;
const channelLogger = console;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const POLL_GAP_MS = 5000;
let pollGapOverride: number | null = null;

export function setPollGapForTest(ms: number | null): void {
  if (ms !== null) {
    const ok = Number.isFinite(ms) && ms > 0;
    if (!ok) {
      throw new Error(
        "setPollGapForTest: gap must be a positive finite number or null",
      );
    }
  }
  pollGapOverride = ms;
}

/** Clears poll-gap test override (shared test teardown). */
export function resetPollGapForTest(): void {
  pollGapOverride = null;
}

export function pollGapMs(): number {
  if (pollGapOverride !== null) {
    return pollGapOverride;
  }
  return POLL_GAP_MS;
}

function deliveryId(thread: string, turnNumber: number): string {
  return `${thread}#${turnNumber}`;
}

async function isDelivered(
  host: HostContext,
  thread: string,
  turnNumber: number,
): Promise<boolean> {
  const key = `${DELIVERED_PREFIX}${deliveryId(thread, turnNumber)}`;
  return (await host.kv.get(key)) === true;
}

async function markDelivered(
  host: HostContext,
  thread: string,
  turnNumber: number,
): Promise<void> {
  const key = `${DELIVERED_PREFIX}${deliveryId(thread, turnNumber)}`;
  await host.kv.put(key, true);
}

type PreparedTurn = {
  thread: string;
  turnNumber: number;
  token: string;
  turnJson: string;
  scoreMd: string;
};

async function prepareTurn(
  host: HostContext,
  thread: string,
  turnNumber: number,
  fallbackScore: string,
): Promise<PreparedTurn | null> {
  if (await isDelivered(host, thread, turnNumber)) {
    await busMarkReadForMailbox(host, thread, turnNumber);
    return null;
  }
  const { turn } = await busGetTurn(host, thread, turnNumber, false);
  if (!turn) {
    return null;
  }
  if (!turnAddressedToMailbox(turn)) {
    channelLogger.info(
      `[bus] skip turn ${thread}#${turnNumber} to=${String(turn.to)} (not ${IMPRESARIO_MAILBOX})`,
    );
    return null;
  }
  if (!shouldStartModelTurn()) {
    await ingestTurnGated(host, thread, turnNumber, channelLogger);
    await markDelivered(host, thread, turnNumber);
    await persistLaneCursor(host, thread, turnNumber);
    await busMarkReadForMailbox(host, thread, turnNumber);
    return null;
  }
  const workKey = workKeyFromTurn(turn);
  const scoreMd = await readScoreMarkdown(host, workKey, fallbackScore);
  const rotation = await getThreadRotation(host, thread);
  return {
    thread,
    turnNumber,
    token: continuationToken(thread, rotation),
    turnJson: turnToJson(turn),
    scoreMd,
  };
}

async function deliverPrepared(
  host: HostContext,
  send: SendMessageFn,
  prep: PreparedTurn,
): Promise<{ coalesced?: boolean; continuationToken?: string }> {
  const session = await send("Handle turn.json", {
    continuationToken: prep.token,
    admission: "coalesce",
    deliveryId: deliveryId(prep.thread, prep.turnNumber),
    workspaceFiles: {
      "turn.json": prep.turnJson,
      "score.md": prep.scoreMd,
    },
  });
  await markDelivered(host, prep.thread, prep.turnNumber);
  await persistLaneCursor(host, prep.thread, prep.turnNumber);
  if (session.coalesced) {
    channelLogger.info(
      `[bus] coalesced:true ${deliveryId(prep.thread, prep.turnNumber)}`,
    );
  }
  await bumpThreadTurnCount(host, prep.thread);
  await busMarkReadForMailbox(host, prep.thread, prep.turnNumber);
  return {
    coalesced: session.coalesced,
    continuationToken: session.continuationToken ?? undefined,
  };
}

async function ingestTurn(
  host: HostContext,
  send: SendMessageFn,
  thread: string,
  turnNumber: number,
  fallbackScore: string,
): Promise<{ coalesced?: boolean; continuationToken?: string } | null> {
  const prep = await prepareTurn(host, thread, turnNumber, fallbackScore);
  if (!prep) {
    return null;
  }
  return deliverPrepared(host, send, prep);
}

function busChannelToolCtx(host: HostContext): ToolContext {
  return {
    session: { purpose: "channel", id: "impresario-bus" },
    host,
  } as unknown as ToolContext;
}

async function pollOnce(args: ChannelStartArgs, fallbackScore: string): Promise<void> {
  const host = args.host;
  const send = args.send;
  const lane = getImpresarioLane();
  const afterTurn = await readLaneCursor(host);

  if (afterTurn > 0) {
    try {
      await busWait(host, lane, afterTurn, 30);
    } catch (err) {
      channelLogger.warn(
        `[bus] ${busTransportLogMeta("wait", err)} wait failed; polling fetch_unread`,
        err,
      );
    }
  }

  const unread = await busFetchUnread(host, IMPRESARIO_MAILBOX, lane, true);
  const items = unread.turns ?? [];
  const prepared: PreparedTurn[] = [];
  for (const item of items) {
    const thread = String(item.thread);
    const turnNumber = Number(item.turn_number);
    const prep = await prepareTurn(host, thread, turnNumber, fallbackScore);
    if (prep) {
      prepared.push(prep);
    }
  }
  if (prepared.length === 0) {
    return;
  }
  for (const prep of prepared) {
    await deliverPrepared(host, send, prep);
  }
}

async function runPollLoop(args: ChannelStartArgs): Promise<void> {
  const scenarioScore = fixturesEnabled() ? loadScenario().score_md : "";
  let backoffMs = pollGapMs();
  while (!pollStopped) {
    try {
      await pollOnce(args, scenarioScore);
      backoffMs = pollGapMs();
      await sleep(pollGapMs());
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 503 || status === 500) {
        if (backoffMs === pollGapMs()) {
          channelLogger.warn("[bus] wait/fetch 5xx; backing off");
        }
        await sleep(backoffMs);
        backoffMs = Math.min(backoffMs * 2, 60_000);
        continue;
      }
      channelLogger.warn(`[bus] ${busTransportLogMeta("poll", err)} poll error`, err);
      await sleep(backoffMs);
    }
  }
}

export default defineChannel({
  auth: [bearerAuth(agentSdkBearerForAuth())],
  events: {
    async "message.completed"(event, _channelCtx, _sessionCtx) {
      const text = event.data.text?.trim() ?? "";
      if (/^STOP\|/.test(text)) {
        channelLogger.info("[bus] message.completed: no further bus post on STOP|");
      }
    },
  },
  routes: [
    POST("/turn", {
      description: "Deliver one bus turn to the impresario (push fallback)",
      bodySchema: z.object({
        thread: z.string().min(1),
        turn_number: z.number().int().positive(),
      }),
      handler: async (_req, ctx) => {
        const host = ctx.host;
        const send = ctx.send;
        const { thread, turn_number: turnNumber } = ctx.body;
        const fallback = "";
        ctx.waitUntil(
          ingestTurn(host, send, thread, turnNumber, fallback).then(() => undefined),
        );
        return new Response(null, { status: 202 });
      },
    }),
  ],
  onStart(args: ChannelStartArgs) {
    pollStopped = false;
    requireAgentSdkBearerToken();
    const stateRoot = impresarioStateRoot();
    args.waitUntil(
      (async () => {
        try {
          await maybePostSeatRegistration(args.host, stateRoot);
        } catch (err) {
          channelLogger.warn("[bus] seat registration failed", err);
        }
        try {
          await bootstrapImpresarioRegistry(busChannelToolCtx(args.host));
        } catch (err) {
          channelLogger.warn("[bus] delivery-bindings bootstrap failed", err);
        }
        if (process.env.ULG_BUS_MODE === "push") {
          return;
        }
        await runPollLoop(args);
      })(),
    );
  },
  onStop() {
    pollStopped = true;
  },
});

export { deliverPrepared, prepareTurn, busWait, pollOnce, runPollLoop };

export function signalPollLoopStop(): void {
  pollStopped = true;
}

export function resetPollLoopForTest(): void {
  pollStopped = false;
}

export { LAST_LANE_TURN_KEY } from "../lib/bus-lane.js";
