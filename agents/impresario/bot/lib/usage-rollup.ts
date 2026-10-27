import type { HostContext } from "@grok/agent-sdk";

const ROLLUP_KEY = "usage:impresario:rollup";

export type UsageRollup = {
  inputTokens: number;
  outputTokens: number;
  gatedTurns: number;
  modelTurns: number;
  updatedAt: string;
};

export async function readUsageRollup(host: HostContext): Promise<UsageRollup> {
  const raw = (await host.kv.get(ROLLUP_KEY)) as UsageRollup | undefined;
  return (
    raw ?? {
      inputTokens: 0,
      outputTokens: 0,
      gatedTurns: 0,
      modelTurns: 0,
      updatedAt: new Date(0).toISOString(),
    }
  );
}

async function writeRollup(host: HostContext, next: UsageRollup): Promise<void> {
  await host.kv.put(ROLLUP_KEY, { ...next, updatedAt: new Date().toISOString() });
}

export async function recordModelUsage(
  host: HostContext,
  inputTokens: number,
  outputTokens: number,
): Promise<void> {
  const cur = await readUsageRollup(host);
  await writeRollup(host, {
    ...cur,
    inputTokens: cur.inputTokens + inputTokens,
    outputTokens: cur.outputTokens + outputTokens,
    modelTurns: cur.modelTurns + 1,
  });
}

export async function recordGatedTurn(host: HostContext): Promise<void> {
  const cur = await readUsageRollup(host);
  await writeRollup(host, {
    ...cur,
    gatedTurns: cur.gatedTurns + 1,
  });
}

export function formatUsageRollup(rollup: UsageRollup): string {
  return [
    `gated_bus_turns=${rollup.gatedTurns}`,
    `model_turns=${rollup.modelTurns}`,
    `input_tokens=${rollup.inputTokens}`,
    `output_tokens=${rollup.outputTokens}`,
  ].join(" · ");
}
