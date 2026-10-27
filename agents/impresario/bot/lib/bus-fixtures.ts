import fs from "node:fs";
import path from "node:path";

import { parseCloseoutExcerpt, truncateBody } from "./ulg.js";

export type BusFixtureScenario = {
  scenario: string;
  turn: Record<string, unknown>;
  score_md: string;
  closeouts: Record<string, { raw: string }>;
  bus_get?: Record<string, unknown>;
};

let cached: BusFixtureScenario | null = null;

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

export function loadScenario(): BusFixtureScenario {
  if (cached) {
    return cached;
  }
  const name = process.env.ULG_FIXTURE_SCENARIO ?? "done-20901-5";
  const file = path.join(fixtureDir(), "bus", `${name}.json`);
  const raw = fs.readFileSync(file, "utf8");
  cached = JSON.parse(raw) as BusFixtureScenario;
  return cached;
}

export function resetFixtureCache(): void {
  cached = null;
}

export function fixtureMcpCall(
  mcpTool: "agent_bus" | "agent_bus_read" | "fs",
  args: Record<string, unknown>,
): unknown {
  const scenario = loadScenario();

  if (mcpTool === "fs") {
    const op = args.op as string;
    const fsPath = args.path as string;
    if (op === "read" && fsPath.includes("impresario-scores/")) {
      const limit = (args.limit as number | undefined) ?? 200;
      const lines = scenario.score_md.split("\n").slice(0, limit);
      return {
        content: lines.join("\n"),
        truncated: lines.length < scenario.score_md.split("\n").length,
      };
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
    if (op === "md_rewrite_section" || op === "md_insert") {
      return { ok: true, stubbed: true };
    }
    return { ok: true, stubbed: true, op };
  }

  if (mcpTool === "agent_bus_read") {
    const op = args.tool as string;
    const payload = (args.payload ?? {}) as Record<string, unknown>;
    if (op === "get") {
      const turn = { ...scenario.turn };
      if (payload.projection === "compact") {
        turn.body = truncateBody(String(turn.body ?? ""));
      }
      return { turn };
    }
    return scenario.bus_get ?? { turns: [] };
  }

  if (mcpTool === "agent_bus") {
    const op = args.tool as string;
    const payload = (args.payload ?? {}) as Record<string, unknown>;
    if (op === "send") {
      return {
        posted: true,
        stubbed: true,
        thread: payload.thread ?? scenario.turn.thread,
        subject: payload.subject,
        to: payload.to,
      };
    }
    if (op === "substrate_graph_write") {
      return { ok: true, stubbed: true };
    }
  }

  return { ok: true, stubbed: true };
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
