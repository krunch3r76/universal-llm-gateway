import path from "node:path";
import { fileURLToPath } from "node:url";

import type { ToolContext } from "@grok/agent-sdk/tools";

import { fixtureMcpCall, fixturesEnabled, loadScenario } from "./bus-fixtures.js";
import { parseHostMcpJson } from "./mcp-result.js";

export type ToolCtx = ToolContext;

export const IMPRESARIO_MAILBOX =
  process.env.ULG_IMPRESARIO_MAILBOX ?? "impresario-sdk";
/** Poll lane — set by env, registry bootstrap, or test prime; never hardcoded. */
export let IMPRESARIO_LANE = process.env.ULG_IMPRESARIO_LANE?.trim() ?? "";
export const COMMISSION_MODEL = "cursor/composer-2.5";
export const MAESTRO_THREAD = "12286";

export type DeliveryBindingEnvelope = {
  state?: string;
  current?: {
    actuation?: Record<string, unknown>;
  };
};

let registryReviewMailbox = "";

export type ResolvedReviewMailbox =
  | { ok: true; mailbox: string }
  | { ok: false; error: string };

/** Registry primed, or non-empty ULG_IMPRESARIO_REVIEW_MAILBOX env override. */
export function resolveImpresarioReviewMailbox(): ResolvedReviewMailbox {
  const envMailbox = process.env.ULG_IMPRESARIO_REVIEW_MAILBOX?.trim();
  if (envMailbox) {
    return { ok: true, mailbox: envMailbox };
  }
  if (registryReviewMailbox) {
    return { ok: true, mailbox: registryReviewMailbox };
  }
  return {
    ok: false,
    error:
      "review recipient unresolved; call bootstrapImpresarioRegistry or primeImpresarioDeliveryRegistry",
  };
}

export function impresarioReviewMailbox(): string {
  const resolved = resolveImpresarioReviewMailbox();
  if (!resolved.ok) {
    throw new Error(resolved.error);
  }
  return resolved.mailbox;
}

/** Hermetic tests: apply registry resolve results without live agent-bus. */
export function primeImpresarioDeliveryRegistry(opts: {
  lane: string;
  reviewMailbox: string;
}): void {
  IMPRESARIO_LANE = opts.lane.trim();
  registryReviewMailbox = opts.reviewMailbox.trim();
}

/** Reset module registry state between hermetic tests. */
export function resetImpresarioDeliveryRegistryForTest(): void {
  IMPRESARIO_LANE = process.env.ULG_IMPRESARIO_LANE?.trim() || "";
  registryReviewMailbox = "";
}

export function impresarioDeliveryHouse(): string {
  const house = process.env.ULG_IMPRESARIO_HOUSE?.trim();
  if (house) {
    return house;
  }
  const lane = process.env.ULG_IMPRESARIO_LANE?.trim();
  if (lane) {
    return lane;
  }
  if (IMPRESARIO_LANE) {
    return IMPRESARIO_LANE;
  }
  throw new Error(
    "ULG_IMPRESARIO_HOUSE or ULG_IMPRESARIO_LANE is required to resolve delivery bindings",
  );
}

function actuationThreadId(envelope: DeliveryBindingEnvelope): string | undefined {
  const act = envelope.current?.actuation;
  if (!act) {
    return undefined;
  }
  for (const key of ["thread_id", "thread", "house_id", "lane_thread"]) {
    const v = act[key];
    if (typeof v === "string" && v.trim() !== "") {
      return v.trim();
    }
  }
  return undefined;
}

function actuationMailbox(envelope: DeliveryBindingEnvelope): string | undefined {
  const act = envelope.current?.actuation;
  if (!act) {
    return undefined;
  }
  for (const key of ["mailbox", "to", "agent"]) {
    const v = act[key];
    if (typeof v === "string" && v.trim() !== "") {
      return v.trim();
    }
  }
  return undefined;
}

function isBoundDelivery(envelope: DeliveryBindingEnvelope): boolean {
  const state = String(envelope.state ?? "").toLowerCase();
  return state === "bound" || state === "current";
}

function fixtureDeliveryBindingResolve(params: {
  house_id: string;
  intent: string;
}): DeliveryBindingEnvelope {
  const scenario = loadScenario();
  const laneThread =
    typeof scenario.turn.thread === "string" ? scenario.turn.thread.trim() : "";
  const intent = params.intent.trim().toLowerCase();
  if (intent === "inform") {
    return {
      state: "bound",
      current: {
        actuation: {
          type: "bus_poll",
          thread_id: laneThread,
        },
      },
    };
  }
  if (intent === "paste") {
    return {
      state: "bound",
      current: {
        actuation: {
          type: "bus_mailbox",
          mailbox: "grok-bot-gc1",
        },
      },
    };
  }
  return { state: "unbound", current: undefined };
}

export async function resolveDeliveryBinding(
  ctx: ToolCtx,
  params: { house_id: string; intent: string },
): Promise<DeliveryBindingEnvelope> {
  if (fixturesEnabled()) {
    return fixtureDeliveryBindingResolve(params);
  }
  const payload = {
    house_id: params.house_id,
    intent: params.intent,
  };
  const raw = await callAgentBusRead(ctx, "delivery_bindings_resolve", payload);
  return raw as DeliveryBindingEnvelope;
}

async function registerImpresarioLaneSubscription(
  ctx: ToolCtx,
  lane: string,
): Promise<void> {
  const agentId = process.env.ULG_IMPRESARIO_AGENT_ID?.trim();
  await callAgentBus(ctx, "subscribe", {
    agent: IMPRESARIO_MAILBOX,
    thread_scope: lane,
    surface: "agent_sdk",
    display: IMPRESARIO_MAILBOX,
    title: `${IMPRESARIO_MAILBOX} poll lane`,
    agent_id: agentId ?? undefined,
    bound_by: IMPRESARIO_MAILBOX,
    evidence: "delivery-bindings case-4 seat registration",
  });
}

/** Register the seat and resolve poll lane + review mailbox via GET /delivery-bindings/resolve. */
export async function bootstrapImpresarioRegistry(ctx: ToolCtx): Promise<void> {
  const envLane = process.env.ULG_IMPRESARIO_LANE?.trim();
  const envReviewMailbox = process.env.ULG_IMPRESARIO_REVIEW_MAILBOX?.trim();
  const house = impresarioDeliveryHouse();

  let lane = envLane ?? "";
  if (!lane) {
    const laneEnvelope = await resolveDeliveryBinding(ctx, {
      house_id: house,
      intent: "inform",
    });
    lane = actuationThreadId(laneEnvelope) ?? "";
    if (!lane && fixturesEnabled()) {
      const turn = loadScenario().turn;
      if (typeof turn.thread === "string") {
        lane = turn.thread.trim();
      }
    }
    if (!lane) {
      throw new Error(
        `delivery-bindings lane unresolved for house=${house} intent=inform`,
      );
    }
  }
  IMPRESARIO_LANE = lane;
  await registerImpresarioLaneSubscription(ctx, lane);

  if (envReviewMailbox) {
    registryReviewMailbox = envReviewMailbox;
    return;
  }

  const reviewHouse = process.env.ULG_IMPRESARIO_REVIEW_HOUSE?.trim() ?? MAESTRO_THREAD;
  const reviewEnvelope = await resolveDeliveryBinding(ctx, {
    house_id: reviewHouse,
    intent: "paste",
  });
  const mailbox = actuationMailbox(reviewEnvelope);
  if (!mailbox || !isBoundDelivery(reviewEnvelope)) {
    throw new Error(
      `delivery-bindings review recipient unresolved for house=${reviewHouse} intent=paste`,
    );
  }
  registryReviewMailbox = mailbox;
}

export function getImpresarioLane(): string {
  const env = process.env.ULG_IMPRESARIO_LANE?.trim();
  if (env) {
    return env;
  }
  if (IMPRESARIO_LANE) {
    return IMPRESARIO_LANE;
  }
  if (fixturesEnabled()) {
    const turn = loadScenario().turn;
    if (typeof turn.thread === "string" && turn.thread.trim()) {
      IMPRESARIO_LANE = turn.thread.trim();
      return IMPRESARIO_LANE;
    }
  }
  throw new Error(
    "impresario lane unresolved; bootstrapImpresarioRegistry or ULG_IMPRESARIO_LANE",
  );
}

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../",
);

export function liveSteerEnabled(): boolean {
  const v = process.env.ULG_LIVE_STEER ?? "0";
  return v === "1" || v.toLowerCase() === "true";
}

/** Host gate: live model turns require ULG_MODEL_TURNS=1 and CURSOR_API_KEY. */
export function modelTurnsEnabled(): boolean {
  const v = process.env.ULG_MODEL_TURNS ?? "0";
  if (v !== "1" && v.toLowerCase() !== "true") {
    return false;
  }
  return Boolean(process.env.CURSOR_API_KEY?.trim());
}

export { busMailbox, type BusMailbox } from "./mailbox.js";

export function repoScriptsPath(rel: string): string {
  return path.join(repoRoot, rel);
}

export function isEvalOrFixture(ctx: ToolCtx): boolean {
  return ctx.session.purpose === "eval" || fixturesEnabled();
}

export {
  resolveScoreUri,
  scoreUri,
  SCORE_WRITE_BYTE_CAP,
} from "./score-guards.js";

export function closeoutUri(dispatchId: string): string {
  return `workspaces://universal-llm-gateway/tmp/reviews/closeouts/${dispatchId}.md`;
}

export function truncateBody(text: string, max = 2000): string {
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max - 1)}…`;
}

export async function callAgentBus(
  ctx: ToolCtx,
  op: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureMcpCall("agent_bus", { tool: op, payload });
  }
  if (isEvalOrFixture(ctx) && op === "send") {
    return { posted: false, stubbed: true, op, ...payload };
  }
  const raw = await ctx.host.mcp.callTool("ulg", "agent_bus", {
    tool: op,
    arguments: JSON.stringify(payload),
  });
  return parseHostMcpJson(raw);
}

export async function callAgentBusRead(
  ctx: ToolCtx,
  op: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureMcpCall("agent_bus_read", { tool: op, payload });
  }
  const raw = await ctx.host.mcp.callTool("ulg", "agent_bus_read", {
    tool: op,
    arguments: JSON.stringify(payload),
  });
  return parseHostMcpJson(raw);
}

export async function callFs(
  ctx: ToolCtx,
  args: Record<string, unknown>,
): Promise<unknown> {
  if (fixturesEnabled()) {
    return fixtureMcpCall("fs", args);
  }
  const raw = await ctx.host.mcp.callTool("ulg", "fs", args);
  return parseHostMcpJson(raw);
}

export function parseCloseoutExcerpt(raw: string): {
  verdict_line?: string;
  status_line?: string;
  residue?: string;
  sections: string[];
} {
  const lines = raw.split("\n");
  const sections: string[] = [];
  let verdict_line: string | undefined;
  let status_line: string | undefined;
  const bodyLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("## effects_manifest")) {
      break;
    }
    if (line.startsWith("## ")) {
      sections.push(line.slice(3).trim());
    }
    if (/^VERDICT:/i.test(line)) {
      verdict_line = line.trim();
    }
    if (/^status:/i.test(line)) {
      status_line = line.trim();
    }
    bodyLines.push(line);
    if (bodyLines.length >= 60) {
      break;
    }
  }
  const residue = bodyLines.join("\n").trim().slice(0, 4500);
  return { verdict_line, status_line, residue, sections };
}
