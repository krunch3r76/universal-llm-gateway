import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";

import readWorkspaceTool, {
  resolveWorkspaceReadPath,
} from "../bot/tools/read_workspace.js";

describe("read_workspace (R2)", () => {
  it("resolveWorkspaceReadPath rejects paths that escape the workspace", () => {
    const ws = path.join(os.tmpdir(), "imp-read-ws");
    const escaped = resolveWorkspaceReadPath(ws, "../outside/turn.json");
    assert.equal(escaped.ok, false);
    if (!escaped.ok) {
      assert.match(escaped.error, /not allowed|escapes workspace/);
    }
  });

  let workspaceDir: string;

  beforeEach(() => {
    workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "imp-read-ws-"));
    fs.writeFileSync(
      path.join(workspaceDir, "turn.json"),
      JSON.stringify({ thread: "20901" }),
    );
  });

  afterEach(() => {
    fs.rmSync(workspaceDir, { recursive: true, force: true });
  });

  it("reads turn.json inside the workspace", async () => {
    const result = await readWorkspaceTool.execute!(
      { path: "turn.json" },
      { workspaceDir } as never,
    );
    assert.match(String((result as { content?: string }).content), /20901/);
  });
});
