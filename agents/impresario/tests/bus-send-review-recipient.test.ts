import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";

import busSendTool, {
  isReviewRequestSubject,
  resolveBusSendInput,
  resolveReviewRequestRecipient,
} from "../bot/tools/bus_send.js";

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
  it("resolveReviewRequestRecipient corrects maestro to grok-bot-gc1", () => {
    const r = resolveReviewRequestRecipient(
      "REQUEST|review|todo:reaper-amendment",
      "maestro",
    );
    assert.equal(r.to, "grok-bot-gc1");
    assert.equal(r.recipient_corrected, true);
  });

  it("resolveReviewRequestRecipient defaults omitted to to grok-bot-gc1", () => {
    const r = resolveReviewRequestRecipient(
      "REQUEST|review|todo:reaper-amendment",
      undefined,
    );
    assert.equal(r.to, "grok-bot-gc1");
    assert.equal(r.recipient_corrected, false);
  });

  it("isReviewRequestSubject is case-insensitive on the head", () => {
    assert.equal(isReviewRequestSubject("REQUEST|Review|todo:x"), true);
    assert.equal(isReviewRequestSubject("  request|review|todo:x  "), true);
    assert.equal(isReviewRequestSubject("DONE|todo:x"), false);
  });
});

describe("bus_send defaults (stage 6 AC2)", () => {
  let workspaceDir: string;

  beforeEach(() => {
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

  it("to omitted on review → grok-bot-gc1", async () => {
    const result = await busSendTool.execute!(
      {
        thread: "20901",
        subject: "REQUEST|review|todo:reaper-amendment",
        body: "review please",
      },
      evalCtx(workspaceDir),
    );
    assert.equal((result as { to: string }).to, "grok-bot-gc1");
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

  it("dryRunResult includes workspaceDir", () => {
    const dryRun = busSendTool.dryRunResult as (input: {
      subject: string;
      thread?: string;
    }) => Record<string, unknown>;
    const out = dryRun({
      subject: "REQUEST|review|todo:x",
      thread: "20901",
    });
    assert.ok("workspaceDir" in out);
  });
});
