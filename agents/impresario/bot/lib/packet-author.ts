/** Verbatim block required on fix packets authored from reviewer verdicts. */
export const BEFORE_YOU_POST_BLOCK = [
  "Before you post.",
  "For each change, name the input or state where it breaks (service down, concurrency, partial failure, wrong ordering, install or staging path) and the test that covers it.",
  "Change only what the brief names; anything else you think should change goes in the review request as a proposal, not in the diff.",
  "Run `git diff --stat <base>..HEAD` and account for every file.",
  "Once the request is posted, stop committing.",
].join(" ");

const FILE_CITE =
  /\b([A-Za-z0-9_./-]+\.(?:ts|tsx|js|md|py|json))(?::(\d+))?\b/g;

const REVIEW_LINE = /^\s*(?:-\s*)?(?:[A-Z]\d+|[\d]+[.)])\s+(.+)$/;

export type FixPacketDraft = {
  scope: string;
  files_expected: string[];
  acceptance: string[];
  review_items: string[];
};

export function extractCitedFiles(verdictBody: string): string[] {
  const seen = new Set<string>();
  const files: string[] = [];
  for (const m of verdictBody.matchAll(FILE_CITE)) {
    const path = m[1]!;
    if (!seen.has(path)) {
      seen.add(path);
      files.push(path);
    }
  }
  return files;
}

export function extractReviewItems(verdictBody: string): string[] {
  const items: string[] = [];
  for (const line of verdictBody.split("\n")) {
    const m = line.match(REVIEW_LINE);
    if (m) {
      items.push(m[1]!.trim());
    }
  }
  if (items.length === 0) {
    for (const line of verdictBody.split("\n")) {
      const trimmed = line.trim();
      if (trimmed.startsWith("- ") && trimmed.length > 2) {
        items.push(trimmed.slice(2).trim());
      }
    }
  }
  return items;
}

export function authorFixPacketFromVerdict(verdictBody: string): FixPacketDraft {
  const review_items = extractReviewItems(verdictBody);
  const cited = extractCitedFiles(verdictBody);
  const filesFromItems = new Set<string>();
  for (const item of review_items) {
    for (const f of extractCitedFiles(item)) {
      filesFromItems.add(f);
    }
  }
  const files_expected =
    cited.length > 0 ? cited : [...filesFromItems].sort();
  const acceptance = review_items.map(
    (item, i) => `AC${i + 1}: Address review — ${item}`,
  );
  if (acceptance.length === 0) {
    acceptance.push("AC1: Address all blocking review items in the verdict.");
  }
  const scope = "Fix blocking gc1 REQUEST_CHANGES items (cited files only).";
  acceptance.push(BEFORE_YOU_POST_BLOCK);
  return {
    scope,
    files_expected: files_expected.slice(0, 20),
    acceptance: acceptance.slice(0, 20),
    review_items,
  };
}

export function filesExpectedSubsetOfCites(
  files_expected: string[],
  verdictBody: string,
): boolean {
  const cited = new Set(extractCitedFiles(verdictBody));
  if (cited.size === 0) {
    return files_expected.length === 0;
  }
  return files_expected.every((f) => cited.has(f));
}
