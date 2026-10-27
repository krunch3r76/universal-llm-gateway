import fs from "node:fs";
import path from "node:path";

import { defineTool } from "@grok/agent-sdk/tools";
import { z } from "zod";

const ALLOWED_BASENAMES = new Set(["turn.json", "score.md"]);

export type WorkspaceReadResolve =
  | { ok: true; fullPath: string }
  | { ok: false; error: string };

/** Resolve a basename under workspaceDir; refuses paths that escape the workspace root. */
export function resolveWorkspaceReadPath(
  workspaceDir: string,
  relativePath: string,
): WorkspaceReadResolve {
  if (!ALLOWED_BASENAMES.has(relativePath)) {
    return {
      ok: false,
      error: `read_workspace: path not allowed: ${relativePath}`,
    };
  }
  const root = path.resolve(workspaceDir);
  const fullPath = path.resolve(root, relativePath);
  if (fullPath !== root && !fullPath.startsWith(`${root}${path.sep}`)) {
    return { ok: false, error: "read_workspace: path escapes workspace" };
  }
  return { ok: true, fullPath };
}

export default defineTool({
  description:
    "Read turn.json or score.md from the session workspace. Call at most once per file per turn when seeded content is insufficient.",
  inputSchema: z.object({
    path: z.enum(["turn.json", "score.md"]),
  }),
  effect: "read",
  async execute(input, ctx) {
    const resolved = resolveWorkspaceReadPath(ctx.workspaceDir, input.path);
    if (!resolved.ok) {
      return { ok: false, error: resolved.error };
    }
    try {
      const content = fs.readFileSync(resolved.fullPath, "utf8");
      return { path: input.path, content };
    } catch {
      return { ok: false, error: `read_workspace: could not read ${input.path}` };
    }
  },
});
