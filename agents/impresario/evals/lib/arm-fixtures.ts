import { loadScenario, resetFixtureCache } from "../../bot/lib/bus-fixtures.js";
import { primeImpresarioDeliveryRegistry } from "../../bot/lib/ulg.js";

/** Arm ULG bus fixtures for one eval case; restores prior env in finally. */
export async function withFixtureScenario(
  fixtureRoot: string,
  scenario: string,
  run: () => void | Promise<void>,
): Promise<void> {
  const prevDir = process.env.ULG_FIXTURE_DIR;
  const prevScenario = process.env.ULG_FIXTURE_SCENARIO;
  try {
    process.env.ULG_FIXTURE_DIR = fixtureRoot;
    process.env.ULG_FIXTURE_SCENARIO = scenario;
    resetFixtureCache();
    const turn = loadScenario().turn;
    const laneThread =
      typeof turn.thread === "string" && turn.thread.trim() !== ""
        ? turn.thread.trim()
        : "20901";
    primeImpresarioDeliveryRegistry({
      lane: laneThread,
      reviewMailbox: "grok-bot-gc1",
    });
    await run();
  } finally {
    if (prevDir === undefined) {
      delete process.env.ULG_FIXTURE_DIR;
    } else {
      process.env.ULG_FIXTURE_DIR = prevDir;
    }
    if (prevScenario === undefined) {
      delete process.env.ULG_FIXTURE_SCENARIO;
    } else {
      process.env.ULG_FIXTURE_SCENARIO = prevScenario;
    }
    resetFixtureCache();
  }
}
