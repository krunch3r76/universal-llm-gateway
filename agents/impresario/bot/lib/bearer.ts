import { fixturesEnabled } from "./bus-fixtures.js";

export const AGENT_SDK_BEARER_REQUIRED = "AGENT_SDK_BEARER_REQUIRED";

/** Fail closed when bearer is unset outside fixture/eval harness. */
export function requireAgentSdkBearerToken(): string {
  if (fixturesEnabled()) {
    return process.env.AGENT_SDK_BEARER_TOKEN?.trim() || "fixture-bearer";
  }
  const token = process.env.AGENT_SDK_BEARER_TOKEN?.trim();
  if (!token) {
    throw new Error(AGENT_SDK_BEARER_REQUIRED);
  }
  return token;
}

export function agentSdkBearerForAuth(): string {
  return process.env.AGENT_SDK_BEARER_TOKEN?.trim() ?? "";
}
