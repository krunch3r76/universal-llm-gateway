import { replaceScoreSectionBody } from "./score-guards.js";

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
