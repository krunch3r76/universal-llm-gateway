import {
  bearerAuth,
  defineChannel,
  POST,
} from "@grok/agent-sdk/channels";
import type { ChannelStartArgs, SendMessageFn } from "@grok/agent-sdk/channels";
import type { HostContext } from "@grok/agent-sdk";
import { z } from "zod";

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
import { IMPRESARIO_LANE, IMPRESARIO_MAILBOX } from "../lib/ulg.js";

const DELIVERED_PREFIX = "delivered:";
const LAST_LANE_TURN_KEY = "bus:last_lane_turn";

async function busCall(
  host: HostContext,
  tool: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureBusOp(tool, payload);
  }
  const mcpTool = tool === "get" ? "agent_bus_read" : "agent_bus";
  return host.mcp.callTool("ulg", mcpTool, {
    tool,
    arguments: JSON.stringify(payload),
  });
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
  compact = true,
): Promise<{ turns?: Record<string, unknown>[] }> {
  return busCall(host, "fetch_unread", {
    to: mailbox,
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

let pollStopped = false;
const channelLogger = console;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    await busGetTurn(host, thread, turnNumber, true);
    return null;
  }
  const { turn } = await busGetTurn(host, thread, turnNumber, false);
  if (!turn) {
    return null;
  }
  await markDelivered(host, thread, turnNumber);
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
  if (session.coalesced) {
    channelLogger.info(
      `[bus] coalesced:true ${deliveryId(prep.thread, prep.turnNumber)}`,
    );
  }
  await bumpThreadTurnCount(host, prep.thread);
  await busGetTurn(host, prep.thread, prep.turnNumber, true);
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

async function pollOnce(args: ChannelStartArgs, fallbackScore: string): Promise<void> {
  const host = args.host;
  const send = args.send;
  const lane = IMPRESARIO_LANE;
  const afterTurn =
    (typeof (await host.kv.get(LAST_LANE_TURN_KEY)) === "number"
      ? (await host.kv.get(LAST_LANE_TURN_KEY))
      : 0) as number;

  await busWait(host, lane, afterTurn, 60);

  const unread = await busFetchUnread(host, IMPRESARIO_MAILBOX, true);
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
  let backoffMs = 5000;
  while (!pollStopped) {
    try {
      await pollOnce(args, scenarioScore);
      backoffMs = 5000;
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 503 || status === 500) {
        if (backoffMs === 5000) {
          channelLogger.warn("[bus] wait/fetch 5xx; backing off");
        }
        await sleep(backoffMs);
        backoffMs = Math.min(backoffMs * 2, 60_000);
        continue;
      }
      channelLogger.warn("[bus] poll error", err);
      await sleep(backoffMs);
    }
  }
}

export default defineChannel({
  auth: [
    bearerAuth(process.env.AGENT_SDK_BEARER_TOKEN ?? "dev-impresario-bearer"),
  ],
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
    if (process.env.ULG_BUS_MODE === "push") {
      return;
    }
    args.waitUntil(runPollLoop(args));
  },
  onStop() {
    pollStopped = true;
  },
});
