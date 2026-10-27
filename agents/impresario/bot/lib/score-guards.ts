export const SCORE_WRITE_BYTE_CAP = 4608;
export const SCORE_READ_CHAR_CAP = 4500;
export const SCORE_READ_LINE_LIMIT = 200;

import path from "node:path";

export const SCORE_URI_PREFIX =
  "cortex://notes/system/threads/impresario-scores/";

const WORK_KEY_RE = /^[a-z0-9][a-z0-9._:-]{0,127}$/;

export type ScoreUriResult =
  | { ok: true; uri: string }
  | { ok: false; reason: "work_key_invalid" };

export type ScoreSizeResult =
  | { ok: true; text: string; bytes: number }
  | {
      ok: false;
      reason: "score_too_large";
      bytes: number;
      cap: number;
    };

export function validateWorkKey(workKey: string): ScoreUriResult | { ok: true } {
  if (
    workKey.startsWith(".") ||
    workKey.includes("/") ||
    workKey.includes("..")
  ) {
    return { ok: false, reason: "work_key_invalid" };
  }
  if (!/^(todo|friction):/.test(workKey)) {
    return { ok: false, reason: "work_key_invalid" };
  }
  if (!WORK_KEY_RE.test(workKey)) {
    return { ok: false, reason: "work_key_invalid" };
  }
  return { ok: true };
}

export function resolveScoreUri(workKey: string): ScoreUriResult {
  const base = validateWorkKey(workKey);
  if (!base.ok) {
    return base;
  }
  const rel = `impresario-scores/${workKey}.md`;
  const normalized = path.posix.normalize(rel);
  if (
    !normalized.startsWith("impresario-scores/") ||
    normalized.includes("..")
  ) {
    return { ok: false, reason: "work_key_invalid" };
  }
  const uri = `${SCORE_URI_PREFIX}${workKey}.md`;
  if (!uri.startsWith(SCORE_URI_PREFIX)) {
    return { ok: false, reason: "work_key_invalid" };
  }
  return { ok: true, uri };
}

/** @deprecated use resolveScoreUri */
export function scoreUri(workKey: string): string {
  const resolved = resolveScoreUri(workKey);
  if (!resolved.ok) {
    throw new Error(resolved.reason);
  }
  return resolved.uri;
}

export function utf8ByteLength(text: string): number {
  return Buffer.byteLength(text, "utf8");
}

export function replaceScoreSectionBody(
  text: string,
  sectionName: string,
  newBody: string,
): string {
  const heading = `## ${sectionName}`;
  const idx = text.indexOf(heading);
  if (idx === -1) {
    throw new Error(`Section not found: ${sectionName}`);
  }
  const lineEnd = text.indexOf("\n", idx);
  const bodyStart = lineEnd === -1 ? text.length : lineEnd + 1;
  const rest = text.slice(bodyStart);
  const next = rest.match(/\n## /);
  const bodyEnd = next ? bodyStart + next.index! : text.length;
  let body = newBody;
  if (body && !body.endsWith("\n")) {
    body += "\n";
  }
  const after = text.slice(bodyEnd);
  if (after && body && !body.endsWith("\n\n")) {
    body += "\n";
  }
  return text.slice(0, bodyStart) + body + after;
}

export function checkScoreSizeAfterUpdate(
  existingMarkdown: string,
  section: string,
  body: string,
): ScoreSizeResult {
  const next = replaceScoreSectionBody(existingMarkdown, section, body);
  const bytes = utf8ByteLength(next);
  if (bytes > SCORE_WRITE_BYTE_CAP) {
    return {
      ok: false,
      reason: "score_too_large",
      bytes,
      cap: SCORE_WRITE_BYTE_CAP,
    };
  }
  return { ok: true, text: next, bytes };
}

export function windowScoreContent(
  content: string,
  lineLimit: number,
  charCap = SCORE_READ_CHAR_CAP,
): { content: string; truncated: boolean } {
  const allLines = content.split("\n");
  let lines = allLines;
  let truncated = false;
  if (lines.length > lineLimit) {
    lines = lines.slice(0, lineLimit);
    truncated = true;
  }
  while (lines.length > 0 && utf8ByteLength(lines.join("\n")) > charCap) {
    lines.pop();
    truncated = true;
  }
  return { content: lines.join("\n"), truncated };
}
