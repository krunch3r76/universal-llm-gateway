import { defineSchedule } from "@grok/agent-sdk/schedules";

import { postDailyUsageStatus } from "../lib/daily-usage-report.js";

export default defineSchedule({
  cron: "0 5 * * *",
  async run(schedule) {
    await postDailyUsageStatus(schedule.host);
  },
});
