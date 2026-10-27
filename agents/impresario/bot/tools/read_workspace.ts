import fs from "node:fs";
import path from "node:path";

import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

import { gateToolCall } from "../lib/tool-call-cap.js";

const ALLOWED_BASENAMES = new Set(["turn.json", "score.md"]);

export default defineTool({
  description:
    "Read turn.json or score.md from the session workspace. Call at most once per file per turn when seeded content is insufficient.",
  inputSchema: z.object({
    path: z.enum(["turn.json", "score.md"]),
  }),
  effect: "read",
  async execute(input, ctx) {
    return gateToolCall(ctx, async () => {
      if (!ALLOWED_BASENAMES.has(input.path)) {
        return { ok: false, error: `read_workspace: path not allowed: ${input.path}` };
      }
      const full = path.resolve(ctx.workspaceDir, input.path);
      const root = path.resolve(ctx.workspaceDir);
      if (!full.startsWith(`${root}${path.sep}`) && full !== root) {
        return { ok: false, error: "read_workspace: path escapes workspace" };
      }
      try {
        const content = fs.readFileSync(full, "utf8");
        return { path: input.path, content };
      } catch {
        return { ok: false, error: `read_workspace: could not read ${input.path}` };
      }
    });
  },
});
