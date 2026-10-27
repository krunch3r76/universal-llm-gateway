import { resetFixtureCache } from "../../bot/lib/bus-fixtures.js";

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
