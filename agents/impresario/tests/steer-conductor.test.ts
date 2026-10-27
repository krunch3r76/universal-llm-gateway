import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, it } from "node:test";

import commissionTool from "../bot/tools/commission.js";
import steerTool from "../bot/tools/steer_conductor.js";
import { resetFixtureCache } from "../bot/lib/bus-fixtures.js";
import { findInFlightRow, reduceLedgerForWorkKey } from "../bot/lib/dispatch.js";

const workKey = "todo:agent-sdk-impresario-probe";
const dispatchId = "80ad0776-0000-4000-8000-000000000099";

function toolCtx(purpose: "tool_test" | "eval" = "tool_test") {
  return {
    session: { purpose, id: "test-session" },
    host: {
      mcp: {
        callTool: async () => ({ stubbed: true }),
      },
    },
  } as never;
}

describe("steer_conductor ledger lookup (AC1)", () => {
  beforeEach(() => {
    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_LIVE_STEER;
    resetFixtureCache();
  });

  it("resolves row via ledger op without throwing when fixtures disabled", async () => {
    const row = {
      dispatch_id: dispatchId,
      status: "running",
      thread_id: "18415",
      work_key: workKey,
      contract: "conductor",
    };
    let steerCalls = 0;
    const ctx = {
      session: { purpose: "tool_test" as const, id: "ledger-steer" },
      host: {
        mcp: {
          callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
            if (tool === "team_dispatch" && args.op === "ledger") {
              return {
                work_key: workKey,
                dispatches: [row],
                house_rollup: { live_dispatches: [row] },
              };
            }
            if (tool === "team_dispatch" && args.op === "steer") {
              steerCalls += 1;
            }
            return { inject_state: "pending" };
          },
        },
      },
    } as never;
    const result = await steerTool.execute!(
      {
        dispatch_id: dispatchId,
        work_key: workKey,
        directive: "fix it",
        reason: "verdict",
      },
      ctx,
    );
    assert.equal((result as { would_steer?: boolean }).would_steer, true);
    assert.equal(steerCalls, 0);
  });

  it("returns structured NOT_FOUND when ledger has no matching row", async () => {
    const ctx = {
      session: { purpose: "tool_test" as const, id: "ledger-miss" },
      host: {
        mcp: {
          callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
            if (tool === "team_dispatch" && args.op === "ledger") {
              return { work_key: workKey, dispatches: [], house_rollup: {} };
            }
            return {};
          },
        },
      },
    } as never;
    const result = await steerTool.execute!(
      {
        dispatch_id: dispatchId,
        work_key: workKey,
        directive: "n/a",
        reason: "test",
      },
      ctx,
    );
    assert.equal((result as { stop?: string }).stop, "NOT_FOUND");
  });
});

describe("steer_conductor dry-run gate (AC2)", () => {
  beforeEach(() => {
    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_LIVE_STEER;
    resetFixtureCache();
  });

  it("returns dry-run with zero steer op calls when live steer is off", async () => {
    const row = {
      dispatch_id: dispatchId,
      status: "running",
      thread_id: "1",
      work_key: workKey,
      contract: "conductor",
    };
    let steerCalls = 0;
    const ctx = {
      session: { purpose: "tool_test" as const, id: "dry-steer" },
      host: {
        mcp: {
          callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
            if (tool === "team_dispatch" && args.op === "steer") {
              steerCalls += 1;
            }
            if (tool === "team_dispatch" && args.op === "ledger") {
              return { dispatches: [row], house_rollup: { live_dispatches: [row] } };
            }
            return {};
          },
        },
      },
    } as never;
    const result = await steerTool.execute!(
      {
        dispatch_id: dispatchId,
        work_key: workKey,
        directive: "apply fix",
        reason: "review",
      },
      ctx,
    );
    assert.equal((result as { posted?: boolean }).posted, false);
    assert.equal((result as { would_steer?: boolean }).would_steer, true);
    assert.equal(steerCalls, 0);
  });
});

describe("ledger in-flight before slice (AC3)", () => {
  it("ROW_PINNED selects live row beyond the first three slim rows", async () => {
    const filler = "x".repeat(520);
    const rows = Array.from({ length: 4 }, (_, i) => ({
      dispatch_id: `d-${i}-${filler}`,
      status: i === 3 ? "running" : "completed",
      thread_id: "99999",
      work_key: workKey,
      contract: "freeform",
    }));
    const raw = {
      work_key: workKey,
      dispatches: rows,
      house_rollup: { live_dispatches: [rows[3]!] },
    };
    const { rows: slim } = reduceLedgerForWorkKey(raw, workKey);
    assert.equal(findInFlightRow(slim)?.dispatch_id, rows[3]!.dispatch_id);

    delete process.env.ULG_FIXTURE_DIR;
    delete process.env.ULG_LIVE_STEER;
    resetFixtureCache();
    let captured: Record<string, unknown> | undefined;
    const ctx = {
      session: { purpose: "tool_test" as const, id: "slice-pin" },
      host: {
        mcp: {
          callTool: async (_ns: string, tool: string, args: Record<string, unknown>) => {
            if (tool === "team_dispatch" && args.op === "ledger") {
              captured = args;
              return raw;
            }
            return {};
          },
        },
      },
    } as never;
    const result = await commissionTool.execute!(
      {
        work_key: workKey,
        contract: "freeform",
        scope: "x",
        files_expected: ["a.ts"],
        acceptance: ["pass"],
      },
      ctx,
    );
    assert.equal((result as { stop?: string }).stop, "ROW_PINNED");
    assert.equal(captured?.op, "ledger");
  });
});
