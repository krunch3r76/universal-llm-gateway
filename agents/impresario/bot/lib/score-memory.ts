import {
  replaceScoreSectionBody,
  resolveScoreUri,
  utf8ByteLength,
  SCORE_WRITE_BYTE_CAP,
} from "./score-guards.js";
import { callFs } from "./ulg.js";
import type { ToolCtx } from "./ulg.js";

const TERMINAL_WORKER_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "closed",
  "terminal",
]);

export type WorkerRecord = {
  dispatch_id?: string;
  thread_id?: string;
  execution_id?: string;
  state?: string;
};

export type StageRecord = {
  commission_at?: string;
  done_at?: string;
  verdict_at?: string;
  verdict?: string;
  next_commission_at?: string;
  composer_cost?: string;
};

export type PersistScoreResult =
  | { ok: true }
  | {
      ok: false;
      reason:
        | "work_key_invalid"
        | "score_too_large"
        | "score_write_failed"
        | "section_not_found";
      detail?: string;
      bytes?: number;
    };

export function parseKeyValueSection(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const m = trimmed.match(/^([a-z_]+):\s*(.+)$/i);
    if (m) {
      out[m[1]!.toLowerCase()] = m[2]!.trim();
    }
  }
  return out;
}

export function extractSectionBody(markdown: string, section: string): string {
  const heading = `## ${section}`;
  const idx = markdown.indexOf(heading);
  if (idx === -1) {
    return "";
  }
  const lineEnd = markdown.indexOf("\n", idx);
  const bodyStart = lineEnd === -1 ? markdown.length : lineEnd + 1;
  const rest = markdown.slice(bodyStart);
  const next = rest.match(/\n## /);
  const bodyEnd = next ? bodyStart + next.index! : markdown.length;
  return markdown.slice(bodyStart, bodyEnd).trim();
}

export function parseWorkerSection(markdown: string): WorkerRecord {
  const kv = parseKeyValueSection(extractSectionBody(markdown, "worker"));
  return {
    dispatch_id: kv.dispatch_id,
    thread_id: kv.thread_id,
    execution_id: kv.execution_id,
    state: kv.state,
  };
}

export function parseStageSection(markdown: string): StageRecord {
  const kv = parseKeyValueSection(extractSectionBody(markdown, "stage"));
  return {
    commission_at: kv.commission_at,
    done_at: kv.done_at,
    verdict_at: kv.verdict_at,
    verdict: kv.verdict,
    next_commission_at: kv.next_commission_at,
    composer_cost: kv.composer_cost,
  };
}

export function formatWorkerSection(worker: WorkerRecord): string {
  const lines: string[] = [];
  if (worker.dispatch_id) {
    lines.push(`dispatch_id: ${worker.dispatch_id}`);
  }
  if (worker.thread_id) {
    lines.push(`thread_id: ${worker.thread_id}`);
  }
  if (worker.execution_id) {
    lines.push(`execution_id: ${worker.execution_id}`);
  }
  if (worker.state) {
    lines.push(`state: ${worker.state}`);
  }
  return lines.length ? `${lines.join("\n")}\n` : "\n";
}

export function formatStageSection(stage: StageRecord): string {
  const lines: string[] = [];
  const keys: (keyof StageRecord)[] = [
    "commission_at",
    "done_at",
    "verdict_at",
    "verdict",
    "next_commission_at",
    "composer_cost",
  ];
  for (const key of keys) {
    const v = stage[key];
    if (v) {
      lines.push(`${key}: ${v}`);
    }
  }
  return lines.length ? `${lines.join("\n")}\n` : "\n";
}

export function isTerminalWorkerState(state: string | undefined): boolean {
  if (!state) {
    return false;
  }
  return TERMINAL_WORKER_STATES.has(state.toLowerCase());
}

export type ResumeFromScore =
  | {
      ok: true;
      dispatch_id: string;
      thread_id: string;
      execution_id?: string;
    }
  | { ok: false; reason: string };

export function resolveResumeFromScore(scoreMarkdown: string): ResumeFromScore {
  const worker = parseWorkerSection(scoreMarkdown);
  const stage = parseStageSection(scoreMarkdown);
  if (!worker.dispatch_id || !worker.thread_id) {
    return { ok: false, reason: "worker_incomplete" };
  }
  if (!isTerminalWorkerState(worker.state)) {
    return { ok: false, reason: "worker_not_terminal" };
  }
  if (stage.verdict !== "REQUEST_CHANGES") {
    return { ok: false, reason: "verdict_not_request_changes" };
  }
  return {
    ok: true,
    dispatch_id: worker.dispatch_id,
    thread_id: worker.thread_id,
    execution_id: worker.execution_id,
  };
}

export function mergeScoreSections(
  markdown: string,
  updates: { worker?: WorkerRecord; stage?: StageRecord },
): string {
  let text = markdown;
  if (updates.worker) {
    const body = formatWorkerSection(updates.worker);
    const replaced = replaceScoreSectionBody(text, "worker", body);
    if (replaced.ok) {
      text = replaced.text;
    } else {
      text = `${text.trimEnd()}\n\n## worker\n${body}`;
    }
  }
  if (updates.stage) {
    const prior = parseStageSection(text);
    const merged: StageRecord = { ...prior, ...updates.stage };
    const body = formatStageSection(merged);
    const replaced = replaceScoreSectionBody(text, "stage", body);
    if (replaced.ok) {
      text = replaced.text;
    } else {
      text = `${text.trimEnd()}\n\n## stage\n${body}`;
    }
  }
  return text;
}

export function stageAfterCommission(
  prior: StageRecord,
  at: string,
): StageRecord {
  return {
    ...prior,
    commission_at: at,
    next_commission_at: at,
  };
}

export function stageAfterCloseout(prior: StageRecord, at: string): StageRecord {
  return { ...prior, done_at: at };
}

export function stageAfterVerdict(
  prior: StageRecord,
  at: string,
  verdict: string,
): StageRecord {
  return { ...prior, verdict_at: at, verdict };
}

export type CommissionResumeValidation =
  | { ok: true; resume_of?: string; reuse_thread?: string }
  | { ok: false; reason: string };

export function validateCommissionResumeFields(input: {
  nest_under?: string;
  resume_of?: string;
  reuse_thread?: string;
  scoreText: string;
}): CommissionResumeValidation {
  const resume = resolveResumeFromScore(input.scoreText);
  let resume_of = input.resume_of;
  let reuse_thread = input.reuse_thread;

  if (resume.ok) {
    if (!resume_of) {
      resume_of = resume.dispatch_id;
    }
    if (!reuse_thread) {
      reuse_thread = resume.thread_id;
    }
  }

  if (input.nest_under && resume_of) {
    return { ok: false, reason: "nest_under_xor_resume_of" };
  }

  if (resume_of && !reuse_thread) {
    return { ok: false, reason: "reuse_thread_required" };
  }

  if (resume_of && reuse_thread && resume.ok) {
    if (resume_of !== resume.dispatch_id) {
      return { ok: false, reason: "resume_of_mismatch" };
    }
    if (reuse_thread !== resume.thread_id) {
      return { ok: false, reason: "reuse_thread_mismatch" };
    }
  } else if (resume_of && reuse_thread) {
    const worker = parseWorkerSection(input.scoreText);
    if (
      worker.dispatch_id === resume_of &&
      worker.thread_id &&
      reuse_thread !== worker.thread_id
    ) {
      return { ok: false, reason: "reuse_thread_mismatch" };
    }
  }

  return { ok: true, resume_of, reuse_thread };
}

async function readScoreContent(
  ctx: ToolCtx,
  workKey: string,
): Promise<string | PersistScoreResult> {
  const resolved = resolveScoreUri(workKey);
  if (!resolved.ok) {
    return { ok: false, reason: "work_key_invalid" };
  }
  const existing = (await callFs(ctx, {
    op: "read",
    path: resolved.uri,
    limit: 500,
  })) as { content?: string };
  return existing.content ?? "";
}

export async function persistScoreMemory(
  ctx: ToolCtx,
  workKey: string,
  updates: { worker?: WorkerRecord; stage?: StageRecord },
): Promise<PersistScoreResult> {
  if (ctx.session.purpose === "eval") {
    return { ok: true };
  }
  const resolved = resolveScoreUri(workKey);
  if (!resolved.ok) {
    return { ok: false, reason: "work_key_invalid" };
  }
  const path = resolved.uri;
  const priorRaw = await readScoreContent(ctx, workKey);
  if (typeof priorRaw !== "string") {
    return priorRaw;
  }
  const merged = mergeScoreSections(priorRaw, updates);
  const bytes = utf8ByteLength(merged);
  if (bytes > SCORE_WRITE_BYTE_CAP) {
    return { ok: false, reason: "score_too_large", bytes };
  }

  const writeFull = (await callFs(ctx, {
    op: "write",
    path,
    content: merged,
  })) as { ok?: boolean; reason?: string; error?: string };
  if (writeFull.ok === false || writeFull.reason || writeFull.error) {
    return {
      ok: false,
      reason: "score_write_failed",
      detail: String(writeFull.reason ?? writeFull.error ?? "write refused"),
    };
  }
  return { ok: true };
}

export async function persistCommissionScoreMemory(
  ctx: ToolCtx,
  workKey: string,
  worker: {
    dispatch_id: string;
    thread_id: string;
    execution_id: string;
    state: string;
  },
): Promise<PersistScoreResult> {
  const now = new Date().toISOString();
  const priorRaw = await readScoreContent(ctx, workKey);
  if (typeof priorRaw !== "string") {
    return priorRaw;
  }
  const stage = stageAfterCommission(parseStageSection(priorRaw), now);
  return persistScoreMemory(ctx, workKey, { worker, stage });
}

export async function harvestCloseoutToScore(
  ctx: ToolCtx,
  workKey: string,
  dispatchId: string,
): Promise<PersistScoreResult & { harvested?: boolean }> {
  if (ctx.session.purpose === "eval") {
    return { ok: true, harvested: false };
  }
  const now = new Date().toISOString();
  const priorRaw = await readScoreContent(ctx, workKey);
  if (typeof priorRaw !== "string") {
    return priorRaw;
  }
  const worker = parseWorkerSection(priorRaw);
  const stage = stageAfterCloseout(parseStageSection(priorRaw), now);
  const workerPatch: WorkerRecord = {
    ...worker,
    dispatch_id: worker.dispatch_id ?? dispatchId,
    state: "completed",
  };
  const result = await persistScoreMemory(ctx, workKey, {
    worker: workerPatch,
    stage,
  });
  return { ...result, harvested: result.ok };
}

const VERDICT_LINE = /^VERDICT:\s*(APPROVE|REQUEST_CHANGES)\b/i;

/** Token from the VERDICT: head line only (no substring fallback). */
export function parseVerdictTokenFromLine(verdict: string): string | undefined {
  for (const line of verdict.split("\n")) {
    const m = line.trim().match(VERDICT_LINE);
    if (m) {
      return m[1]!.toUpperCase();
    }
  }
  return undefined;
}

export async function harvestVerdictToScore(
  ctx: ToolCtx,
  workKey: string,
  verdict: string,
): Promise<PersistScoreResult & { harvested?: boolean }> {
  if (ctx.session.purpose === "eval") {
    return { ok: true, harvested: false };
  }
  const verdictToken = parseVerdictTokenFromLine(verdict);
  if (!verdictToken) {
    return { ok: false, reason: "score_write_failed", detail: "verdict_line_unrecognized" };
  }
  const now = new Date().toISOString();
  const priorRaw = await readScoreContent(ctx, workKey);
  if (typeof priorRaw !== "string") {
    return priorRaw;
  }
  const stage = stageAfterVerdict(parseStageSection(priorRaw), now, verdictToken);
  const result = await persistScoreMemory(ctx, workKey, { stage });
  return { ...result, harvested: result.ok };
}
