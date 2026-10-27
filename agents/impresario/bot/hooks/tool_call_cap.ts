import { defineHook } from "@grok/agent-sdk/hooks";

import { resetToolCallCapForSession } from "../lib/tool-call-cap.js";

export default defineHook({
  events: {
    async "turn.started"(_event, ctx) {
      await resetToolCallCapForSession(ctx.host, ctx.session.id);
    },
  },
});
