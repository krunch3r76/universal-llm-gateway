import { deriveRunFacts, type EvalTurnResult } from "@grok/agent-sdk/evals";

import {
  checkE4CloseoutRun,
  type E4RuleResult,
} from "./e4-rule-check.js";

/** Run K109 E4 rule check on one settled eval turn (typed session events). */
export function e4RuleFromTurn(
  turn: EvalTurnResult,
  workKey: string,
): E4RuleResult {
  const facts = deriveRunFacts(turn.events);
  return checkE4CloseoutRun(
    facts.toolCalls.map((c) => ({
      toolName: c.toolName,
      args: (c.input ?? {}) as Record<string, unknown>,
      output: c.output,
    })),
    workKey,
  );
}
