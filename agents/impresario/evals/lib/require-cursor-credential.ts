/** E4 and model-turn evals defer until CURSOR_API_KEY lands (F-8). */
export function skipWithoutCursorCredential(t: {
  skip: (reason: string) => never;
}): void {
  if (process.env.CURSOR_API_KEY?.trim()) {
    return;
  }
  t.skip("pending-credential");
}
