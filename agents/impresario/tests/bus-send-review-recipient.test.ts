import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";

import {
  primeImpresarioDeliveryRegistry,
  resetImpresarioDeliveryRegistryForTest,
} from "../bot/lib/ulg.js";
import busSendTool, {
  busSendDryRunResult,
  busSendResponsePosted,
  isReviewRequestSubject,
  parseSubjectTokens,
  resolveBusSendInput,
  resolveReviewRequestRecipient,
  reviewRequestRecipient,
} from "../bot/tools/bus_send.js";

/** Registry resolve fixture (delivery-bindings intent=paste), not a production default. */
const REGISTRY_REVIEW_MAILBOX = "grok-bot-gc1";

function evalCtx(workspaceDir: string) {
  return {
    session: { purpose: "eval" as const, id: "eval-test" },
    workspaceDir,
    host: {
      kv: {
        get: async () => undefined,
        put: async () => undefined,
        delete: async () => undefined,
      },
      mcp: { callTool: async () => ({}) },
    },
  } as never;
}

describe("bus_send REQUEST|review| recipient (stage 5)", () => {
  beforeEach(() => {
    primeImpresarioDeliveryRegistry({
      lane: "20901",
      reviewMailbox: REGISTRY_REVIEW_MAILBOX,
    });
  });

  it("resolveReviewRequestRecipient corrects maestro to registry review mailbox", () => {
    const r = resolveReviewRequestRecipient(
      "REQUEST|review|todo:reaper-amendment",
      "maestro",
    );
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.to, reviewRequestRecipient());
      assert.equal(r.to, REGISTRY_REVIEW_MAILBOX);
      assert.equal(r.recipient_corrected, true);
    }
  });

  it("resolveReviewRequestRecipient defaults omitted to to registry review mailbox", () => {
    const r = resolveReviewRequestRecipient(
      "REQUEST|review|todo:reaper-amendment",
      undefined,
    );
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.to, reviewRequestRecipient());
      assert.equal(r.recipient_corrected, false);
    }
  });

  it("isReviewRequestSubject uses trimmed pipe tokens", () => {
    assert.equal(isReviewRequestSubject("REQUEST|Review|todo:x"), true);
    assert.equal(isReviewRequestSubject("  request | Review |x"), true);
    assert.equal(isReviewRequestSubject("REQUEST|review"), true);
    assert.equal(isReviewRequestSubject("DONE|todo:x"), false);
    assert.deepEqual(parseSubjectTokens(" request | Review |x"), [
      "request",
      "Review",
      "x",
    ]);
  });
});

describe("bus_send defaults (stage 6 AC2)", () => {
  let workspaceDir: string;

  beforeEach(() => {
    primeImpresarioDeliveryRegistry({
      lane: "20901",
      reviewMailbox: REGISTRY_REVIEW_MAILBOX,
    });
    workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-bus-send-"));
    fs.writeFileSync(
      path.join(workspaceDir, "turn.json"),
      JSON.stringify({ thread: "20901", work_key: "todo:x" }),
    );
  });

  afterEach(() => {
    fs.rmSync(workspaceDir, { recursive: true, force: true });
  });

  it("thread omitted → defaulted from turn.json", async () => {
    const resolved = resolveBusSendInput({
      subject: "REQUEST|review|todo:reaper-amendment",
      workspaceDir,
    });
    assert.equal(resolved.ok, true);
    if (resolved.ok) {
      assert.equal(resolved.thread, "20901");
      assert.equal(resolved.thread_defaulted, true);
    }
    const result = await busSendTool.execute!(
      {
        subject: "REQUEST|review|todo:reaper-amendment",
        body: "review please",
      },
      evalCtx(workspaceDir),
    );
    assert.equal((result as { thread: string }).thread, "20901");
    assert.equal((result as { thread_defaulted: boolean }).thread_defaulted, true);
  });

  it("review pins thread to turn.json when model supplies another thread", () => {
    const resolved = resolveBusSendInput({
      subject: "REQUEST|review|todo:reaper-amendment",
      thread: "16416",
      workspaceDir,
    });
    assert.equal(resolved.ok, true);
    if (resolved.ok) {
      assert.equal(resolved.thread, "20901");
      assert.equal(resolved.thread_pinned, true);
    }
  });

  it("non-review refuses thread that does not match turn.json", () => {
    const resolved = resolveBusSendInput({
      subject: "DONE|todo:x|ok",
      thread: "19627",
      to: "maestro",
      workspaceDir,
    });
    assert.equal(resolved.ok, false);
    if (!resolved.ok) {
      assert.match(resolved.error, /19627/);
    }
  });

  it("to omitted on review → registry review mailbox", async () => {
    const result = await busSendTool.execute!(
      {
        thread: "20901",
        subject: "REQUEST|review|todo:reaper-amendment",
        body: "review please",
      },
      evalCtx(workspaceDir),
    );
    assert.equal((result as { to: string }).to, reviewRequestRecipient());
    assert.equal((result as { recipient_corrected?: boolean }).recipient_corrected, false);
  });

  it("to omitted on DONE| → error", async () => {
    const resolved = resolveBusSendInput({
      subject: "DONE|todo:x|ok",
      thread: "20901",
      workspaceDir,
    });
    assert.equal(resolved.ok, false);
    if (!resolved.ok) {
      assert.match(resolved.error, /to is required/);
    }
    const result = await busSendTool.execute!(
      {
        thread: "20901",
        subject: "DONE|todo:x|ok",
        body: "done",
      },
      evalCtx(workspaceDir),
    );
    assert.equal((result as { posted: boolean }).posted, false);
    assert.match(String((result as { error?: string }).error), /to is required/);
  });
});

describe("bus_send thread validation (stage 8)", () => {
  let workspaceDir: string;

  beforeEach(() => {
    primeImpresarioDeliveryRegistry({
      lane: "20901",
      reviewMailbox: REGISTRY_REVIEW_MAILBOX,
    });
    workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-bus-send-"));
  });

  afterEach(() => {
    fs.rmSync(workspaceDir, { recursive: true, force: true });
  });

  it("no turn.json and no thread arg → error", () => {
    const resolved = resolveBusSendInput({
      subject: "DONE|todo:x|ok",
      to: "maestro",
    });
    assert.equal(resolved.ok, false);
    if (!resolved.ok) {
      assert.match(resolved.error, /thread is required/);
    }
  });

  it("malformed turn.json → error", () => {
    fs.writeFileSync(path.join(workspaceDir, "turn.json"), "{not-json");
    const resolved = resolveBusSendInput({
      subject: "REQUEST|review|todo:x",
      workspaceDir,
    });
    assert.equal(resolved.ok, false);
    if (!resolved.ok) {
      assert.match(resolved.error, /valid JSON/);
    }
  });

  it("busSendDryRunResult includes the session workspaceDir path", () => {
    fs.writeFileSync(
      path.join(workspaceDir, "turn.json"),
      JSON.stringify({ thread: "20901" }),
    );
    const out = busSendDryRunResult(
      { subject: "REQUEST|review|todo:x" },
      workspaceDir,
    );
    assert.equal(out.workspaceDir, workspaceDir);
    assert.equal(out.thread, "20901");
  });

  it("busSendTool.dryRunResult uses ctx.workspaceDir and turn.json thread", () => {
    fs.writeFileSync(
      path.join(workspaceDir, "turn.json"),
      JSON.stringify({ thread: "20901" }),
    );
    type DryRunFn = (
      input: { subject: string; thread?: string },
      ctx: { workspaceDir: string },
    ) => ReturnType<typeof busSendDryRunResult>;
    const dryRun = busSendTool.dryRunResult as DryRunFn;
    const out = dryRun(
      { subject: "REQUEST|review|todo:reaper-amendment" },
      { workspaceDir },
    );
    assert.equal(out.workspaceDir, workspaceDir);
    assert.equal(out.thread, "20901");
    assert.equal(out.posted, false);
    const missingCtx = busSendTool.dryRunResult as (
      input: { subject: string },
      ctx?: { workspaceDir?: string },
    ) => { posted: boolean; error?: string };
    const refused = missingCtx({ subject: "REQUEST|review|todo:x" });
    assert.equal(refused.posted, false);
    assert.match(String(refused.error), /workspaceDir/);
  });
});

describe("bus_send posted ack (stage 9 N2)", () => {
  it("busSendResponsePosted accepts turn.id or turn_number", () => {
    assert.equal(
      busSendResponsePosted({ turn: { id: 99, turn_number: 3 } }).ok,
      true,
    );
    assert.equal(busSendResponsePosted({ turn_number: 7 }).ok, true);
  });

  it("busSendResponsePosted rejects empty or malformed bodies", () => {
    assert.equal(busSendResponsePosted({}).ok, false);
    assert.equal(busSendResponsePosted(null).ok, false);
  });

  it("execute returns posted:false when bus response lacks turn confirmation", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-bus-ack-"));
    fs.writeFileSync(
      path.join(workspaceDir, "turn.json"),
      JSON.stringify({ thread: "20901" }),
    );
    try {
      const result = await busSendTool.execute!(
        {
          subject: "DONE|todo:x|ok",
          to: "maestro",
          thread: "20901",
          body: "done",
        },
        {
          session: { purpose: "tool_test" as const, id: "bus-ack-test" },
          workspaceDir,
          host: {
            kv: {
              get: async () => undefined,
              put: async () => undefined,
              delete: async () => undefined,
            },
            mcp: {
              callTool: async () => ({
                content: [{ type: "text", text: '{"ok":true}' }],
              }),
            },
          },
        } as never,
      );
      assert.equal((result as { posted: boolean }).posted, false);
      assert.match(String((result as { error?: string }).error), /turn confirmation/);
    } finally {
      fs.rmSync(workspaceDir, { recursive: true, force: true });
    }
  });
});

describe("bus_send unprimed review recipient (15851#3431)", () => {
  let workspaceDir: string;
  const envSnapshot: Record<string, string | undefined> = {};

  beforeEach(() => {
    for (const key of [
      "ULG_IMPRESARIO_REVIEW_MAILBOX",
      "ULG_IMPRESARIO_LANE",
    ] as const) {
      envSnapshot[key] = process.env[key];
      delete process.env[key];
    }
    resetImpresarioDeliveryRegistryForTest();
    workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-bus-unprimed-"));
    fs.writeFileSync(
      path.join(workspaceDir, "turn.json"),
      JSON.stringify({ thread: "20901", work_key: "todo:x" }),
    );
  });

  afterEach(() => {
    fs.rmSync(workspaceDir, { recursive: true, force: true });
    for (const key of Object.keys(envSnapshot) as (keyof typeof envSnapshot)[]) {
      if (envSnapshot[key] === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = envSnapshot[key];
      }
    }
    primeImpresarioDeliveryRegistry({
      lane: "20901",
      reviewMailbox: REGISTRY_REVIEW_MAILBOX,
    });
  });

  it("unprimed review execute returns posted:false and does not throw", async () => {
    const result = await busSendTool.execute!(
      {
        subject: "REQUEST|review|todo:reaper-amendment",
        body: "review please",
      },
      evalCtx(workspaceDir),
    );
    assert.equal((result as { posted: boolean }).posted, false);
    assert.match(
      String((result as { error?: string }).error),
      /review recipient unresolved/,
    );
  });
});
