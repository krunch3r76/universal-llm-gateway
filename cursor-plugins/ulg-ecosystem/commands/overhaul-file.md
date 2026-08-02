Per-file pre-overhaul pass: split, review, docstring, verify, handoff.
Use this to unblock a single high-risk SLOC violator before running the
full parent-directory `/overhaul`.

## Usage

```
/overhaul-file {file}
```

Where `{file}` is a path relative to project root (e.g.,
`services/universal-stargate/systems/proxy/core/nonstreaming/federated_routing.py`).

## Instructions

Execute steps in order. Each step must complete before moving to the next.
Step 2 (deep split) and step 3 (review) each manage their own approval flow
internally — do not bypass either. Default posture matches `/overhaul` gradual
mode (web-claude-first); use `/modularize` only when the user explicitly opts
into team-generate.

### 1. Validate target and baseline scan

```bash
source ~/.venvs/universal/bin/activate
test -f {file}
scripts/modularize scan {file}
```

Confirm this file is a red (>400) or yellow (301-400) candidate, or has
clear modularity debt even if already below threshold.

### 1.5. Dead code scan (file + local siblings)

```bash
FILE_DIR="$(dirname "{file}")"
vulture "{file}" "${FILE_DIR}" vulture_whitelist.py --min-confidence 80
```

Delete confirmed dead code before splitting so plan quality improves.
Known false positives: `getattr()` dispatch, FastAPI route handlers, event
handler callbacks, and `__init__.py` re-exports.

### 2. Deep split (gradual default: web-claude)

Build a modularize packet per `/overhaul` §2.1 and post to `claude-web` via
`agent_bus`. Claude Web returns the plan on-thread; **Cursor applies** the
approved split locally after audit (`/modularize` §2.6 checks).

Use `/modularize {file}` (team-generate E2E) only when the user explicitly
opts into frontier mode and frontier dispatch is verified.

When the split completes, the file should be green (≤300 SLOC across the new
package), consumer imports rewired, and invariant violations corrected. If
web-claude has not replied, stop — do not fall back to bulk pipeline silently.

Optional legacy bulk path (user opt-in only):

```
scripts/modularize plan {file}
```

Underspecified bulk plans, PHANTOM symbols, and invariant replication are the
caller's responsibility to repair.

### 3. Code review (gradual default: web-claude)

Collect impacted Python files (minimum: target file, newly created sibling
modules, and all changed consumers):

```bash
git diff --name-only -- '*.py' | sort
```

**Gradual (default)**: build a review packet per `/overhaul` step 4 web-claude
path; post to `claude-web`; triage thread findings under Applied / Pending /
Rejected / Suggestions.

**Frontier opt-in**: follow `/consult-review` instructions on that file list.

### 3.5. Observability-first noise reduction

During refactors, prefer event-driven observability and keep logs low-noise:

- If behavior is already captured by structured events, demote repetitive
  request-path logs (`info`/`warning`) to `debug` unless they indicate an
  actionable incident boundary.
- Keep at most one concise summary log per branch where possible; avoid
  per-candidate or per-loop high-volume logs at `info` level.
- Do not remove diagnostically critical logs without ensuring equivalent event
  coverage exists.

When reviewing suggestions, prioritize adding high-value events over adding
more logs. High-value event candidates typically include:

- Branch boundaries (which strategy/path was selected and why)
- Retryable vs permanent failure boundaries
- Queue lifecycle transitions (queued/dequeued/timeout/cancelled)
- Fallback/overflow/handoff transitions between gateways
- Invariant guard blocks (state conditions that prevented an action)
- Recovery transitions (failure -> healthy/unblocked)

### 4. Docstring pass (targeted)

Ensure every touched module, class, and public function has quality
docstrings. Standards match `/overhaul`:

- Module/class docstrings: at least 15 words
- Function docstrings: at least 10 words
- Explain purpose, caller context, invariants, and side effects where relevant

### 4.5. Verify docstring quality

Run both file-level and directory-level checks:

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-quality check {file}
scripts/docstring-quality scan "$(dirname "{file}")"
```

If critical issues are reported (exit code 1), fix and re-run.

### 4.6. Optional cloud docstring enhancement pass

If the target file still has weak/high-noise docstrings after manual pass,
run the pipeline-backed enhancer on the file:

```bash
/docstring-enhance {file}
```

This enables iterative prompt tuning in:
- `pipelines/docstring_enhance/prompts.yaml`
- `pipelines/docstring_enhance/models.yaml`

After applying proposed edits, re-run step 4.5 before moving to quality gates.

### 5. Quality gates (touched files)

```bash
source ~/.venvs/universal/bin/activate
ruff check --select=UP --fix {file}
ruff format {file}
python -m compileall -q "$(dirname "{file}")"
ruff check "$(dirname "{file}")"
```

### 6. Unused imports

```bash
ruff check --select F401 "$(dirname "{file}")"
```

Fix remaining unused imports in touched files.

### 7. Verify split outcome

`/modularize` already runs a final SLOC verification in its §3 (Cursor
Verifies) step. Re-confirm here as a final cross-check before handoff to
the parent directory pass:

```bash
scripts/modularize scan {file}
scripts/modularize scan "$(dirname "{file}")"
```

The original target path may now be a package directory (`{file}/__init__.py`).
All files in the new package should be ≤300 SLOC. If anything remains red,
document why a further split is deferred and surface it to the user before
proceeding.

### 8. Handoff reminder: parent directory `/overhaul`

This command is a pre-pass. It does not complete subsystem architecture-doc
generation by itself. Immediately run the parent directory command next:

```bash
/overhaul {parent_directory}
```

Derive parent directory from target file:

```bash
PARENT_DIR="$(dirname "{file}")"
echo "Next: /overhaul ${PARENT_DIR}/"
```

During parent `/overhaul`, complete doc generation (step 9), human doc review
(step 10), and frontier review (step 11) so `docs/architecture/{subsystem}.md`
is updated from the improved docstrings.

## Rules

- Do not skip the parent-directory `/overhaul` after this pre-pass
- Do not bypass `/modularize` at step 2 — it owns plan, approval, and execute
  for the structural split. The legacy `scripts/modularize plan` path remains
  available only with explicit user opt-in.
- Do not silently fall back to the legacy bulk pipeline if `/modularize`
  halts because Stargate or the frontier dispatcher is unavailable — surface
  the failure and ask the user how to proceed.
- Do not apply suggestion-level `/consult-review` findings without approval
- Do not skip docstring quality verification; doc-generate quality depends on it
- Do not modify `scripts/modularize`; the bulk pipeline still backs `/overhaul`'s
  batch tier and stays as-is
- Process one file at a time with this command
- Prefer events over high-frequency logs; keep request-path logging low noise
