/** Accepted work_key namespace prefixes (shared by tools and score guards). */
export const WORK_KEY_SCHEMES =
  "todo|friction|plan|plan_phase|packet|agent-bus|decision" as const;

export const WORK_KEY_ZOD_REGEX = new RegExp(`^(${WORK_KEY_SCHEMES}):`);

export const WORK_KEY_PREFIX_RE = new RegExp(`^(${WORK_KEY_SCHEMES}):`);

export function isAcceptedWorkKey(value: string): boolean {
  return WORK_KEY_PREFIX_RE.test(value);
}
