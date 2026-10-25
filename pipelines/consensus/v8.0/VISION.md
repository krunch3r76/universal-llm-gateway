# consensus + isolated synthesis (pipeline v8.0)

Proposers from mixed model families give breadth. The contamination-isolated synthesizer stops claims repeated across proposals from passing unchecked, and modal hardening keeps uncertain claims hedged.

## Context

v7.1 delivered and validated:

| Step | Status |
|---|---|
| `review_coherence` (assess_loop_v1) | DONE |
| `find_uncited_filter` deterministic handler | DONE |
| `c4_enforce` (assert_then_revise_v1) | DONE |
| `post_coherence_repair` (Option 2) | DONE — validated 2026-03-02 |

The items below are deferred from v7.1. They are ordered by priority and
remain valid — no architecture or contract changes in v7.1 invalidate them.

---

## Closeout Status (Covered So Far)

Latest pin-first verification for this closeout:
- Fixture: `tools/pipeline_test/fixtures/consensus-chain-v8.0_closeout-pin.json`
- Execution: `e1fc0894-e7f0-4f36-b8ef-c05ed9aebced`
- Verified path: `synthesize__reorganize_answer -> synthesize__citation_repair -> synthesize__c4_enforce -> synthesize__review_coherence -> synthesize__post_coherence_repair`

### Closed from the current v8.0 quality phase

1. **Pre-review information-loss regression** — closed  
   `c4_enforce` no longer strips clinically important continuation content in the
   validated pin path; sentence retention from `reorganize_answer` through final
   synth output is preserved.

2. **Trailing citation compliance before coherence review** — closed  
   `citation_repair` now enforces trailing numeric citations with pinned evidence:
   zero missing trailing citations after citation repair and in final output.

3. **Small-model citation stability (3.8B/7B class)** — closed  
   Current `citation_repair` prompt behavior is deterministic on pinned replay
   (identical repeated outputs), with no `[N]` placeholder leakage and no
   correction-note/footer artifacts.

### Partially closed

- **Readability/flow quality** — improved to acceptable baseline, but still
  somewhat choppy. Remaining polish should occur in `reorganize_answer`, not
  in the mechanical `citation_repair` stage.

### Notes on priority-map alignment

- Priority 3 item 6 (`review_coherence` split revise actions) is implemented and
  active in v8.0.
- Priority 1 item 1 (`c4_check` deterministic handler inside assessor) became
  lower urgency after introducing `citation_repair` + deterministic uncited scan
  + `c4_enforce` before coherence review. Keep as architectural cleanup, not an
  immediate blocker.

---

## Priority 1 — Dehallucination hardening

### 1. `c4_check` deterministic handler

**What**: Use deterministic C4 checking as the authoritative judge and feed its
`revision_targets` directly into revise. Keep revise as the actuator, not the
judge.

**Why**: `find_uncited_filter` already proves each sentence is uncited via
deterministic regex (`re.search(r'\[[^\]]*\d[^\]]*\]', s)`). There is nothing
for an LLM to verify at this point. Observed failures prior to c4_enforce:

| Run | c4_check behavior | Impact |
|---|---|---|
| Photosynthesis (`d22000c9`) | Examined wrong sentence | Actual uncited sentence unverified |
| Metformin (`2dc70853`) | Hallucinated `[8, 14, 15, 58]` onto correct sentence | Concluded HAS CITATION on uncited sentence |

**Note**: With `c4_enforce` now in place, uncited sentences are handled before
`review_coherence`. The remaining architectural cleanup is to make C4 fully
deterministic for decisioning while preserving targeted auto-repair.

**Handler logic**:

```python
def handle(inputs: dict) -> dict:
    uncited = inputs.get("uncited_sentences", [])
    if not uncited:
        return {"c4_result": "pass", "revision_targets": []}
    return {"c4_result": "fail", "revision_targets": uncited}
```

**Selected architecture direction**:

1. Deterministic C4 step emits `c4_result` + `revision_targets` from
   `uncited_sentences` (no LLM judgment for C4).
2. On fail, run revise directly with explicit `revision_targets`.
3. Re-run deterministic C4 check (bounded retries) before entering/continuing
   coherence review.
4. Keep `review_coherence` focused on C1/C2/C3/C5 only (C4 removed from assessor).

**Non-goal for this item**: removing revise entirely. The goal is deterministic
decisioning + targeted repair, not fail-fast deletion-only behavior.

**Verification**: replay photosynthesis and metformin. Confirm uncited sentence
→ deterministic fail → targeted revise → deterministic pass; clean path → no
change. Confirm no information-loss regression and no citation-placeholder
regression on Round-14 (`787724f8`) style cases.

---

### 2. Split `verify_specific` into factual and specificity passes

**What**: Replace the combined `verify_specific` / `verify_batch_specific` prompts with two
sequential single-task calls per model per claim:

1. **Factual pass**: Is the claim factually accurate? → `{factual: bool, factual_reasoning: str}`
2. **Specificity pass**: Is the claim specific to the topic? → `{specific: bool, specific_reasoning: str}`

Acceptance policy unchanged: claim passes only when BOTH `factual=true` AND `specific=true`.

**Why**: The current prompt asks a verifier model to reason about two distinct judgment
dimensions in a single pass with 4–5 required output fields. For 7–8B models in the verifier
pool, this produces conflated reasoning — the model may pass specificity because factual
reasoning succeeded, or generate coherent-looking output where one dimension was not genuinely
evaluated. Two focused calls give each judgment the model's full attention and produce
independent reasoning chains that are individually auditable.

**Design**:

Two prompt pairs (4 new prompts):
- `verify_factual` / `verify_batch_factual` — factual accuracy only, 2 output fields
- `verify_specificity` / `verify_batch_specificity` — topic specificity only, 2 output fields

Handler change (`verify_general.py`): per claim/batch, run factual pass first. Specificity
pass may be gated (skip for factually-false claims — they fail regardless) or run independently
for full audit coverage. Decide at implementation time. Combine results before passing to
`filter_threshold`.

**Verification**: replay photosynthesis and metformin fixtures. Per-claim verdicts for
unambiguous claims must match baseline. Borderline claims where one dimension was masked in
the combined pass may diverge — inspect `verdicts_by_model` diff to confirm the split
surfaces genuinely independent judgments.

---

## Priority 2 — Synthesis precision

### 3. `filter_negatives` false-positive fix

**What**: Narrow the orphaned sub-claim detection rule to pronoun/demonstrative
subjects only. Invert instruction order so the named-entity exclusion runs first.

**Observed failure** (fixture `consensus-chain-v7_7398adeb`): qwen flagged 26
of 53 verified facts as orphaned — 49% removal. All removed claims had
named-entity subjects (Metformin, AMPK, eGFR thresholds, creatinine clearance,
GSK-3). The KEY RULE ("named entity → never orphaned") is not reliably followed
by qwen when it appears after the positive-match rule.

**Root cause**: standard "rule-then-negation" failure for small LLMs on long
input lists. The model learns the primary heuristic and ignores the exception.

**Fix — invert instruction order**:

```
ORPHANED SUB-CLAIMS (return their indices in orphaned_indices):
Your primary check is the grammatical subject.

1. CHECK FOR NAMED ENTITIES (DO NOT RETURN):
   A claim is NEVER orphaned if its grammatical subject is a named entity:
   specific drug, substance, organ, medical condition, patient group, or mechanism.

   NOT orphaned (do NOT return):
   - "NSAIDs can lead to renal impairment" — specific drug class
   - "Metformin activates AMPK" — specific drug and mechanism
   - "AMPK inhibits glucose production" — specific mechanism
   - "Creatinine clearance is a measure of renal filtering" — specific test

2. CHECK FOR VAGUE SUBJECTS (RETURN THESE) — only if step 1 does not apply:
   A claim is ORPHANED if its subject is a vague pronoun or indefinite
   determiner: "It", "They", "These conditions", "Some factors",
   "Various interactions", "This process", "Certain medications".
```

**Verification**: replay `filter_negatives` against `consensus-chain-v7_7398adeb.json`.
`orphaned_indices` must be empty or near-empty. Reference baseline:
`metformin-048b9daa` (filter_negatives removed 0/52 facts).

---

### 4. `identify_redundancy` re-enablement

**What**: Re-enable `identify_redundancy` (`enabled: false` in
`synthesize/synthesize.yaml`) with cross-section false-positive suppression.

**Infrastructure already in place** (no changes needed):
- `synthesize_batched.py` outputs `batch_texts: list[str]`
- `identify_redundancy.py` `_compose_draft()` injects `=== SECTION N ===` markers
- `synthesize.yaml` wires `batch_texts` into `identify_redundancy` inputs

**Remaining work**:

**Step 1 — Handler: discard same-section merge groups** (`identify_redundancy.py`):

```python
# In _parse_merge_groups — after building citations list:
sections = [int(s) for s in g.get("sections", []) if str(s).isdigit()]
if len(set(sections)) < 2:
    continue  # same-section false positive — discard
groups.append({"theme": str(g.get("theme", "")), "citations": sorted(citations)})
```

**Step 2 — System prompt** (`identify_redundancy_context` in `prompts.yaml`):

Add:
```
- The draft contains section markers like === SECTION N ===. A merge group is
  only valid if its citations come from at least 2 different sections.
- Do NOT create merge groups for citations within the same section.
- A single sentence with multiple citations (e.g. "X is Y [3, 8]") is NOT a
  merge group.
- Treat general and specific restatements as redundant if they share the same
  core claim.
- Output exactly one JSON object with exactly one top-level key "merge_groups".
```

**Step 3 — User prompt** (`identify_redundancy_initial` in `prompts.yaml`):

Replace current single-scenario example with multi-faceted block:
```
Example:
- SENTENCE A (Section 1): "X increases Y [1, 10]."
- SENTENCE B (Section 1): "X also enhances Z [11]."
- SENTENCE C (Section 3): "X elevates Y [16]."
- SENTENCE D (Section 4): "W is cleared by the kidneys [20]."

Sentences A and C are redundant — same claim, different sections → merge.
Sentences A and B are NOT redundant — same section, do not merge.

Output JSON only:
{"merge_groups": [
  {"theme": "X increases Y", "sections": [1, 3], "citations": [1, 10, 16]},
  {"theme": "W renal clearance", "sections": [1, 4], "citations": [3, 20]}
]}
```

**Step 4 — Enable** (`synthesize/synthesize.yaml`):
```yaml
- name: identify_redundancy
  enabled: true   # was: false
```

**Verification**: replay `synthesize__identify_redundancy` against
`consensus-chain-v7_b558dce3.json`. Known false positives `[43, 44]`
(same-section) must be discarded. True cross-section groups must be retained.

**Reference**: `pipelines/consensus/v7.1/identify-redundancy-novel-approaches.md`

---

### 5. `reassess_critique` + resynthesize pass

**What**: After `post_coherence_repair`, a critic step reads the final passage and identifies
(1) internal contradictions between sentences and (2) unclear or ambiguous passages. The
synthesizer then reruns with both the verified facts and the critique to produce a
contradiction-free, clarified output.

**Why**: `review_coherence` targets citation coverage (C4) and restriction-language structure
(C1) — not semantic consistency within the passage. Verified facts from multiple originators
can express conflicting positions on the same claim; the synthesizer may preserve the
contradiction rather than resolve it. The critic makes contradictions and unclear passages
explicit so the resynthesizer can fix them using the full verified fact set as ground truth.

**Design**:

Critic step (`reassess_critique`):
- Input: final passage from `post_coherence_repair`
- Task: identify (1) pairs of sentences that directly contradict each other within the passage;
  (2) sentences or clauses that are unclear, ambiguous, or underspecified
- Output: `{contradictions: list[str], unclear_passages: list[str]}`
- Constraint: only flag direct textual contradictions — do not re-verify against sources
- Temperature: 0.0
- Model: phi4

Resynthesize step (`reassess_synthesize`):
- Input: passage + `verified_facts` + `contradictions` + `unclear_passages`
- Gate: `contradictions` empty AND `unclear_passages` empty → skip; pass passage through unchanged
- Task: rewrite only the flagged sections; use verified facts to resolve contradictions;
  clarify unclear passages using fact text; preserve all citation markers
- Model: phi4

**Implementation options** (decide at implementation time):

1. Two `generate` steps: critique step → `consensus_assert_then_revise_v8_0` with critique fields
   injected as inputs
2. Single `consensus_reassess_v8_0` custom handler that encapsulates both passes

Option 2 is preferred — the revise action needs access to `verified_facts`, which is not a
standard `assert_then_revise` input. A custom handler owns the full context without extending
the generic revise contract.

**Synthesize sub-pipeline wiring** (`synthesize/synthesize.yaml`):

```yaml
- name: reassess_critique
  type: generate
  model_ref: phi4
  prompt_ref: consensus.v8.0.reassess_critique
  handler_inputs:
    artifact: post_coherence_repair.raw
  generation_parameters:
    temperature: 0.0
    response_format:
      type: json_object
      schema:
        type: object
        properties:
          contradictions: {type: array, items: {type: string}}
          unclear_passages: {type: array, items: {type: string}}
        required: [contradictions, unclear_passages]
  timeout_seconds: 60

- name: reassess_synthesize
  type: consensus_reassess_v8_0
  model_ref: phi4
  prompt_ref: consensus.v8.0.reassess_revise
  handler_inputs:
    artifact: post_coherence_repair.raw
    verified_facts: inputs.verified_facts
    contradictions: reassess_critique.json.contradictions
    unclear_passages: reassess_critique.json.unclear_passages
  generation_parameters:
    response_format:
      type: text
  timeout_seconds: 120
```

**Verification**: Replay a fixture with known synthesis contradictions. `contradictions` must be
non-empty; `reassess_synthesize.raw` must resolve the flagged contradiction with citations
intact. Replay a clean fixture; output must be identical to `post_coherence_repair.raw` (gate
suppresses the resynthesize call).

---

## Priority 3 — Structural loop repair (framework change)

### 6. Split `review_coherence` revise actions (Option 1)

**What**: Extend `assess_loop_v1` to support an ordered list of sequential
revise actions per iteration, replacing the current single-action revise.

**Why**: The v7.1 C1 structural damage failure (`969e97fa`) was caused by a
3.8B model attempting C1 restriction-language rewrite and structural coherence
preservation in a single revision pass. These conflict: C1 rewrites require
restructuring; coherence requires preserving structure. `post_coherence_repair`
(Option 2) is a validated downstream safety net. Option 1 addresses the root
cause at the loop level.

**Design**:

```yaml
actions:
  revise:
    - prompt_ref: consensus.v8.0.review_coherence_revise_c1
      model_ref: ministral_3_8b
    - prompt_ref: consensus.v8.0.review_coherence_revise_structure
      model_ref: ministral_3_8b
  accept: (terminal)
```

Per iteration: run `revise_c1` (restriction language only, preserve all
paragraph boundaries and citations), then `revise_structure` (structural repair
only, preserve restriction language and citations). The assessor sees the result
of both sub-actions before deciding accept/revise.

**Requires**: `assess_loop_v1` handler change to iterate over an ordered list
of revise action definitions rather than a single action.

**Validation fixture**: `969e97fa` (metformin post-c4_enforce). Compare to
`2dc70853` (1/2 review_coherence iterations, clean output).

**Note**: Once implemented, `post_coherence_repair` may become redundant.
Keep it in place until Option 1 is validated across multiple fixtures — it
costs ~2–4s and provides structural guarantees regardless of loop behavior.

---

## Priority 4 — Domain-conditional routing (infrastructure)

### 7. Conditional model alias resolver

**What**: New resolution rule type in the model alias resolver:

```yaml
model_ref: "{medical: medgemma_4b, default: qwen}"
```

The resolver reads a named context field (e.g. `analyze_question.json.domain`)
and selects the alias branch matching the value, falling back to `default`.

∀ question domain ∉ {medical}: behavior identical to v7.1.

This is the infrastructure gate for items 6–8.

---

### 8. `domain_verify` activation

**What**: Assign MedGemma 4B to the `domain_verify` slot for medical questions:

```yaml
model_ref: "{medical: medgemma_4b, default: null}"
```

`domain_verify` currently runs 0 calls in all observed executions. When
`default: null`, the step no-ops for non-medical domains — preserving v7.1
behavior. For medical facts: dedicated domain-specialist verification before
`synergize`.

No handler changes. YAML change only. **Requires**: resolver (item 7).

---

### 9. `reorganize_answer` conditional model swap

```yaml
model_ref: "{medical: medgemma_4b, default: ministral_3b}"
```

Medical content has implicit clinical paragraph ordering:
mechanism → pharmacokinetics → indications → contraindications → monitoring → interactions.

MedGemma follows this structure without explicit ordering instructions, reducing
prompt complexity and the risk of uncited summary sentences. **Requires**: resolver.

---

### 10. `answer_all` slot swap (lower priority)

```yaml
answer_models:
  qwen: qwen
  gemma_2_9b: gemma_2_9b
  llama_3_1_8b: "{medical: medgemma_4b, default: llama_3_1_8b}"
```

For medical questions: qwen (quantitative) + gemma_2_9b (general reasoning) +
medgemma_4b (domain specialist). Requires `analyze_question` to expose `domain`
in its JSON output, or a fast domain pre-screen before `answer_all`. **Requires**:
resolver + question domain field.

---

## Priority 5 — Schema hardening

### 11. `contraindication_strength` schema field

**What**: Add `contraindication_strength: absolute | conditional` and
`contraindication_condition: str` to the fact schema during `contextualize`.

```python
{
    "text": "Metformin is contraindicated in decompensated heart failure",
    "contraindication_strength": "conditional",
    "contraindication_condition": "decompensated heart failure",
    ...
}
```

**Why**: The pipeline currently flattens absolute and conditional restrictions
into a single list. Evaluated output (Grok coherence 7.5/10) specifically flags
contraindication hierarchy as the deepest structural gap.

With this field:
- `synthesize_answer` passes strength as per-fact metadata
- `reorganize_answer` sorts facts by strength when building contraindications paragraph
- `review_coherence` assessor can verify ordering mechanically (not via prose heuristics)

**Scope**: handler + prompt changes to `contextualize`; fact schema; `synthesize_answer`
per-fact context formatting. High effort — implement after resolver (item 7) enables
domain-routing improvements.

---

## Implementation Order

| Priority | Item | Change type | Requires |
|---|---|---|---|
| 1 | `c4_check` deterministic handler | Handler (or extend `find_uncited_filter`) | `find_uncited_filter` output (done) |
| 1 | Split `verify_specific` into factual + specificity passes | Handler (`verify_general.py`) + 4 prompts + `verify.yaml` | Nothing new |
| 2 | `filter_negatives` false-positive fix | Prompt | Nothing new |
| 2 | `identify_redundancy` re-enablement | Handler + prompts + `synthesize.yaml` | Nothing new |
| 2 | `reassess_critique` + resynthesize | Custom handler + 2 prompts + `synthesize.yaml` | `verified_facts` already in synthesize inputs (done) |
| 3 | Split `review_coherence` revise actions | `assess_loop_v1` framework + 2 prompts | Nothing new |
| 4 | Conditional alias resolver | Infrastructure | Nothing new |
| 4 | `domain_verify` activation | YAML | Resolver (item 7) |
| 4 | `reorganize_answer` model swap | YAML | Resolver (item 7) |
| 4 | `answer_all` slot swap | YAML + `analyze_question` | Resolver + domain field |
| 5 | `contraindication_strength` field | Handler + schema + prompts | None, high effort |

---

## Non-Goals for v8.0

- Changes to the verification chain structure (verify_link0/1/2)
- New pipeline steps beyond the items above

---

## Future improvements (v9 or later)

### 10. `classify_drops` semantic subsumption hint

**What**: Add a context hint to the `classify_drops` prompt so that when a cited
fact uses a **general category** (e.g. "X converts non-carbohydrate sources into
glucose"), uncited facts that express **specific instances** of that category
(e.g. "X converts lactate into glucose", "X converts amino acids and lactate
into glucose") are classified as `subsumed by [K]` rather than `silent drop`.

**Why**: With the v8.0 split (synthesize_answer → classify_drops), the 7B
classify_drops model sometimes labels domain-specific subsumption as "silent
drop" because the vocabulary differs (general term vs. specific substrates).
The final answer is semantically correct — the content is present under the
broader citation — but the metadata is imprecise. A prompt hint reduces
false "silent drop" labels and improves audit/observability without changing
answer prose.

**Scope**: Prompt-only change to `classify_drops` in `prompts.yaml`. Optional:
accept as residual 7B noise if the hint does not materially improve
classification; the pipeline postcondition (`excluded_without_reason = []`)
already holds.

---

## Structural Changes from v7.1

- `shared/` renamed to `_lib/` — these are version-internal utility modules,
  not shared across versions

---

## Carry-Forward — Chained multi-model patterns

These are optional opportunities identified from the consult-chain pattern
(sequential model analysis where model 2 sees model 1's full output).
Not v8.0 scope — carry forward for consideration in v9 or when the relevant
infrastructure is built.

### 11. Chained verification escalation (split-verdict tie-breaking)

**What**: When `verify_general` produces split verdicts (e.g., 2 accept / 2 reject
from the 4-model verifier pool), re-evaluate the contested claim in chained mode:
model 1 verifies with reasoning, model 2 reviews model 1's verdict + reasoning.

**Why**: Independent parallel voting is optimal for throughput but produces
deadlocks on genuinely ambiguous claims. Chained mode produces more calibrated
decisions because the second model can identify reasoning errors in the first
model's verdict rather than independently arriving at the same error.

**Scope**: Selective escalation only — most claims pass majority vote cleanly.
Triggered by `filter_threshold` when a claim's verdict count is exactly tied
or within 1 vote of the threshold. The escalation result breaks the tie.

**Infrastructure**: New `chain_verify` step type (or escalation flag on
`verify_general` / `filter_threshold`). Could reuse the `_augment_with_prior`
pattern from `tools/pipeline_test/consult.py`. The consult discovery
(`chained-consultation-emergent-division-of-labor`) showed 40-75% more
actionable findings in chained vs parallel — the same dynamic should
improve verification accuracy on contested claims.

**Invariant**: ∀ chained escalation: use different model families. Same model
twice produces diminishing returns.

---

### 12. Chained synthesis assessment

**What**: Replace the single-model `review_coherence` assessment with a
two-model chained assessment before the reviser acts. Model A assesses the
draft, model B reviews model A's assessment and adds missed issues.

**Why**: The assess loop's effectiveness is bounded by the assessor's ability
to find all issues in one pass. Chaining the assessment phase gives the
second model a head start — it reads the first model's analysis and focuses
on gaps rather than re-deriving from scratch.

**Scope**: Modify `assess_loop_v1` to support a multi-model assessment phase:
`assess_pool: [qwen, phi4]` runs them sequentially in the assessment step
(not in the revise step, which stays single-model). Model B's assessment
is what the reviser sees.

**Relation to item 6**: Orthogonal to the split-revise-actions proposal.
Item 6 splits the *revise* phase into sub-actions. This splits the *assess*
phase into sequential models. Both can coexist.

---

### 13. Code review pipeline with chained validation

**What**: A structured pipeline (virtual model ID `code-review`) that
replaces the ad-hoc model calls in `scripts/consult -r reviewer` with
a 3-step engine: review (structured JSON critique) → validate (chained,
second model confirms/rejects/augments) → merge (deterministic).

**Why**: Today's `/consult-review` sends raw files to models, gets free-text
prose, and relies on the agent to parse severity/targets/fixes. The pipeline
produces structured JSON output with JSON schema enforcement, built-in chained
validation, and replayability via `pipeline_test`. `/consult-review` becomes
a thin consumer that calls the pipeline and presents results — minimal synthesis.

**Scope**: See `standalone-code-review-pipeline` in todos DB (`scripts/todos show standalone-code-review-pipeline`)
for full Option C spec (pipeline YAML, output schema, categories, consumer integration,
implementation order). Local-first model ordering: local analyst, cloud reviewer.

---

## Reference

- v7.1 VISION.md: `pipelines/consensus/v7.1/VISION.md`
- v7.1 FROZEN.md: `pipelines/consensus/v7.1/FROZEN.md`
- filter_negatives failure: fixture `consensus-chain-v7_7398adeb.json` (26/53 facts removed)
- identify_redundancy novel approaches: `pipelines/consensus/v7.1/identify-redundancy-novel-approaches.md`
- assess_loop_v1 handler: `services/universal-stargate/systems/pipeline/core/handlers/assess_loop.py`
- MedGemma model card: https://huggingface.co/google/medgemma-4b-it
- Coherence evaluation: Grok critique of execution `fcaaa137` (2026-02-28), 7.5/10
- C1 structural damage: fixture `969e97fa` (post-c4_enforce), external assessor 7/10
