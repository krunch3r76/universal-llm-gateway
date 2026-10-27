import { defineHook } from "@grok/agent-sdk/hooks";

import { maybeRotateForTokens } from "../lib/score.js";

function threadFromContinuationToken(token: string | undefined): string | undefined {
  if (!token) {
    return undefined;
  }
  const m = /^thread:([^:]+):r\d+$/.exec(token);
  return m?.[1];
}

export default defineHook({
  events: {
    async "turn.completed"(event, ctx) {
      const usage = event.data.usage;
      if (!usage || ctx.session.purpose === "eval") {
        return;
      }
      await ctx.host.kv.put(`usage:${ctx.session.id}`, {
        inputTokens: usage.inputTokens,
        outputTokens: usage.outputTokens,
        at: event.at,
      });
      const thread = threadFromContinuationToken(
        ctx.session.continuationKey ?? undefined,
      );
      if (thread) {
        await maybeRotateForTokens(ctx.host, thread, usage.inputTokens);
      }
    },
  },
});
