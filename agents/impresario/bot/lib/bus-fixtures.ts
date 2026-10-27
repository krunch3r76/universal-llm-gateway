import fs from "node:fs";
import path from "node:path";

import { replaceScoreSectionBody } from "./score-guards.js";
import { parseCloseoutExcerpt, truncateBody } from "./ulg.js";

export type BusFixtureTurn = Record<string, unknown>;

export type BusFixtureScenario = {
  scenario: string;
  turn: BusFixtureTurn;
  turns?: BusFixtureTurn[];
  score_md: string;
  scores?: Record<string, string>;
  closeouts: Record<string, { raw: string }>;
  bus_get?: Record<string, unknown>;
  ledger?: Record<string, { dispatches: LedgerRow[] }>;
  steer?: Record<string, { inject_state: string; ticket?: string }>;
  wait?: { status?: number; error?: string };
  flags?: {
    defer_mark_read?: boolean;
    skip_mark_read_for?: string[];
  };
};

let cached: BusFixtureScenario | null = null;
const fixtureScoreOverrides = new Map<string, string>();
const fixtureLedgerLive = new Map<string, LedgerRow[]>();
const fixtureBusSendLog: Array<Record<string, unknown>> = [];

export type LedgerRow = {
  dispatch_id: string;
  status: string;
  thread_id?: string;
  work_key?: string;
  contract?: string;
  closeout_stop_tokens?: string[];
};
let unreadQueue: BusFixtureTurn[] = [];
let waitLogged503 = false;
const markReadSkipped = new Set<string>();

export function fixturesEnabled(): boolean {
  return Boolean(process.env.ULG_FIXTURE_DIR);
}

function fixtureDir(): string {
  const root = process.env.ULG_FIXTURE_DIR;
  if (!root) {
    throw new Error("ULG_FIXTURE_DIR is not set");
  }
  return path.isAbsolute(root) ? root : path.resolve(process.cwd(), root);
}

function initUnreadQueue(scenario: BusFixtureScenario): void {
  if (unreadQueue.length > 0) {
    return;
  }
  if (scenario.turns && scenario.turns.length > 0) {
    unreadQueue = scenario.turns.map((t) => ({ ...t }));
  } else {
    unreadQueue = [{ ...scenario.turn }];
  }
}

export function loadScenario(): BusFixtureScenario {
  if (cached) {
    return cached;
  }
  const name = process.env.ULG_FIXTURE_SCENARIO ?? "done-20901-5";
  const file = path.join(fixtureDir(), "bus", `${name}.json`);
  const raw = fs.readFileSync(file, "utf8");
  cached = JSON.parse(raw) as BusFixtureScenario;
  initUnreadQueue(cached);
  return cached;
}

export function resetFixtureCache(): void {
  cached = null;
  unreadQueue = [];
  waitLogged503 = false;
  markReadSkipped.clear();
  fixtureScoreOverrides.clear();
  fixtureLedgerLive.clear();
  fixtureBusSendLog.length = 0;
}

export function fixtureBusSendLogSnapshot(): Array<Record<string, unknown>> {
  return [...fixtureBusSendLog];
}

export function fixtureScoreSnapshot(workKey: string): string {
  const scenario = loadScenario();
  if (fixtureScoreOverrides.has(workKey)) {
    return fixtureScoreOverrides.get(workKey)!;
  }
  return scoreForWorkKey(scenario, workKey);
}

function turnKey(turn: BusFixtureTurn): string {
  return `${turn.thread}#${turn.turn_number}`;
}

function findTurn(
  scenario: BusFixtureScenario,
  thread: string,
  turnNumber: number,
): BusFixtureTurn | undefined {
  const all = [
    ...(scenario.turns ?? []),
    scenario.turn,
  ];
  return all.find(
    (t) => String(t.thread) === thread && Number(t.turn_number) === turnNumber,
  );
}

function scoreForWorkKey(
  scenario: BusFixtureScenario,
  workKey: string | undefined,
): string {
  if (workKey && scenario.scores?.[workKey]) {
    return scenario.scores[workKey]!;
  }
  return scenario.score_md;
}

export function fixtureBusOp(
  op: string,
  payload: Record<string, unknown>,
): unknown {
  const scenario = loadScenario();
  initUnreadQueue(scenario);

  if (op === "wait") {
    if (scenario.wait?.status === 503) {
      if (!waitLogged503) {
        waitLogged503 = true;
      }
      const err = new Error("fixture bus wait 503");
      (err as Error & { status: number }).status = 503;
      throw err;
    }
    return { ok: true, timed_out: unreadQueue.length === 0 };
  }

  if (op === "fetch_unread") {
    return {
      turns: unreadQueue.map((t) => ({
        thread: t.thread,
        turn_number: t.turn_number,
        from: t.from,
        to: t.to,
        subject: t.subject,
        status: t.status ?? "open",
      })),
    };
  }

  if (op === "get") {
    const thread = String(payload.thread);
    const turnNumber = Number(payload.turn_number);
    const base = findTurn(scenario, thread, turnNumber);
    if (!base) {
      return { turn: undefined };
    }
    const turn = { ...base };
    if (payload.projection === "compact") {
      turn.body = truncateBody(String(turn.body ?? ""));
    }
    const markRead = payload.mark_read === true;
    const key = turnKey(turn);
    const skipList = scenario.flags?.skip_mark_read_for ?? [];
    const shouldSkip =
      scenario.flags?.defer_mark_read ||
      skipList.includes(key) ||
      markReadSkipped.has(key);
    if (markRead && !shouldSkip) {
      unreadQueue = unreadQueue.filter(
        (t) =>
          !(
            String(t.thread) === thread &&
            Number(t.turn_number) === turnNumber
          ),
      );
    }
    if (markRead && shouldSkip && skipList.includes(key)) {
      markReadSkipped.add(key);
    }
    return { turn };
  }

  if (op === "send") {
    const rec = {
      posted: true,
      stubbed: true,
      thread: payload.thread ?? scenario.turn.thread,
      subject: payload.subject,
      to: payload.to,
      body: payload.body,
    };
    fixtureBusSendLog.push(rec);
    return rec;
  }

  if (op === "substrate_graph_write") {
    return { ok: true, stubbed: true };
  }

  return { ok: true, stubbed: true, op };
}

export function fixtureMcpCall(
  mcpTool: "agent_bus" | "agent_bus_read" | "fs",
  args: Record<string, unknown>,
): unknown {
  const scenario = loadScenario();
  initUnreadQueue(scenario);

  if (mcpTool === "fs") {
    const op = args.op as string;
    const fsPath = args.path as string;
    if (op === "read" && fsPath.includes("impresario-scores/")) {
      const workKey =
        fsPath.split("impresario-scores/").pop()?.replace(".md", "") ?? "";
      const md = fixtureScoreSnapshot(workKey);
      const limit = (args.limit as number | undefined) ?? 200;
      const lines = md.split("\n").slice(0, limit);
      return {
        content: lines.join("\n"),
        truncated: lines.length < md.split("\n").length,
      };
    }
    if (op === "read" && fsPath.includes("fixtures/closeouts/")) {
      const id = fsPath.split("/").pop()?.replace(".md", "") ?? "";
      const file = path.join(fixtureDir(), "closeouts", `${id}.md`);
      if (fs.existsSync(file)) {
        const raw = fs.readFileSync(file, "utf8");
        const limit = (args.limit as number | undefined) ?? 60;
        return { content: raw.split("\n").slice(0, limit).join("\n") };
      }
    }
    if (op === "md_list" && fsPath.includes("closeouts/")) {
      const id = fsPath.split("/").pop()?.replace(".md", "") ?? "";
      const closeout = scenario.closeouts[id];
      return {
        sections: closeout ? ["summary", "effects_manifest"] : [],
      };
    }
    if (op === "read" && fsPath.includes("closeouts/")) {
      const id = fsPath.split("/").pop()?.replace(".md", "") ?? "";
      const closeout = scenario.closeouts[id];
      if (!closeout) {
        return { content: "", error: "closeout_not_found" };
      }
      const limit = (args.limit as number | undefined) ?? 60;
      const clipped = closeout.raw.split("\n").slice(0, limit).join("\n");
      return { content: clipped };
    }
    if (op === "md_rewrite_section" && fsPath.includes("impresario-scores/")) {
      const workKey =
        fsPath.split("impresario-scores/").pop()?.replace(".md", "") ?? "";
      const section = String(args.section ?? "");
      const content = String(args.content ?? "");
      const prior = fixtureScoreSnapshot(workKey);
      const next = replaceScoreSectionBody(prior, section, content);
      fixtureScoreOverrides.set(workKey, next);
      return { ok: true, rewritten: true };
    }
    if (op === "md_rewrite_section" || op === "md_insert") {
      return { ok: true, stubbed: true };
    }
    return { ok: true, stubbed: true, op };
  }

  if (mcpTool === "agent_bus_read") {
    const op = args.tool as string;
    const payload = (args.payload ?? {}) as Record<string, unknown>;
    return fixtureBusOp(op, payload);
  }

  if (mcpTool === "agent_bus") {
    const op = args.tool as string;
    const payload = (args.payload ?? {}) as Record<string, unknown>;
    return fixtureBusOp(op, payload);
  }

  return { ok: true, stubbed: true };
}

function ledgerRowsForWorkKey(
  scenario: BusFixtureScenario,
  workKey: string,
): LedgerRow[] {
  const base = scenario.ledger?.[workKey]?.dispatches ?? [];
  const live = fixtureLedgerLive.get(workKey) ?? [];
  return [...base, ...live];
}

export function fixtureFindDispatch(dispatchId: string): LedgerRow | undefined {
  const scenario = loadScenario();
  for (const entry of Object.values(scenario.ledger ?? {})) {
    const hit = entry.dispatches.find((d) => d.dispatch_id === dispatchId);
    if (hit) {
      return hit;
    }
  }
  for (const rows of fixtureLedgerLive.values()) {
    const hit = rows.find((d) => d.dispatch_id === dispatchId);
    if (hit) {
      return hit;
    }
  }
  return undefined;
}

export function fixtureTeamDispatch(
  payload: Record<string, unknown>,
): unknown {
  const scenario = loadScenario();
  const op = String(payload.op ?? "");

  if (op === "ledger") {
    const workKey = String(payload.work_key ?? "");
    const rows = ledgerRowsForWorkKey(scenario, workKey);
    const live = rows.filter((r) =>
      ["queued", "admitted", "running", "parked_waiting"].includes(
        String(r.status),
      ),
    );
    return {
      work_key: workKey,
      dispatches: rows,
      house_rollup: { live_dispatches: live, live_count: live.length },
    };
  }

  if (op === "generate") {
    const workKey = String(payload.work_key ?? "");
    const id = `fixture-${workKey}-${fixtureLedgerLive.size + 1}`;
    const row: LedgerRow = {
      dispatch_id: id,
      status: "running",
      thread_id: "worker-fixture",
      work_key: workKey,
      contract: String(payload.contract ?? "freeform"),
    };
    const prior = fixtureLedgerLive.get(workKey) ?? [];
    fixtureLedgerLive.set(workKey, [...prior, row]);
    return {
      dispatch_id: id,
      execution_id: `exec-${id}`,
      stubbed: true,
      posted: false,
    };
  }

  if (op === "steer") {
    const dispatchId = String(payload.dispatch_id ?? "");
    const canned = scenario.steer?.[dispatchId];
    if (canned) {
      return { inject_state: canned.inject_state, ticket: canned.ticket };
    }
    return { inject_state: "pending", ticket: "fixture-steer-ticket" };
  }

  return { ok: true, stubbed: true, op };
}

export function closeoutFromFixture(dispatchId: string): ReturnType<
  typeof parseCloseoutExcerpt
> {
  const scenario = loadScenario();
  const closeout = scenario.closeouts[dispatchId];
  if (!closeout) {
    return { sections: [] };
  }
  return parseCloseoutExcerpt(closeout.raw);
}
