---
trigger_match_terms: ["provenance-granularity", "provenance_granularity", "model_id", "string", "is_independent", "gate", "review-reasoning", "touching", "libs", "provenance", "cross-model", "verification"]
description: On any task touching libs/provenance::is_independent, cross-model verification, or constructing the model_id string passed into a provenance record — read before composing changes.
---

# Provenance Granularity — Family/Version Contract

**Authority**: universal — applies on any task touching `libs/provenance::is_independent`, cross-model verification, the §5.2 independence gate, or any code that constructs the `model_id` string passed into a provenance record.

---

## The rule

`Provenance.originator_model_id` and the `evaluator_model_id` argument to
`is_independent` MUST be at **family/version granularity** or finer.

| Granularity | Examples | Allowed? |
|---|---|---|
| Family/version | `openai/gpt-5.5`, `anthropic/claude-opus-4-7`, `google/gemini-2.5-pro` | ✅ Yes |
| Worker alias (local) | `phi4`, `qwen3-30b-vl` | ✅ Yes — finer than family/version |
| Seat-decorated | `cursor`, `web-anthropic` | ❌ No |
| Session-decorated | `gpt-5.5-session-abc123` | ❌ No |
| Platform-decorated | `openai-azure-eastus` | ❌ No |

Seat, session, or platform decorators MUST NOT be baked into the
comparison string. **Same model on different seats does NOT satisfy
independence.** See spec § 10.5 of
`docs/architecture/entity-backed-claim-provenance.md` for the empirical
illustration that motivated this rule.

---

## Why

The independence gate (`libs/provenance/assertions.is_independent`) is a
pure string comparison: `originator_model_id != evaluator_model_id`.
There is no normalization layer (as of 2026-05). The contract therefore
lives in caller discipline — every producer of provenance must construct
the `model_id` string at the right granularity.

If a caller embeds seat or session info, two outputs from the same model
running in different seats would compare as "independent" — a false
positive that breaks the spec's cross-model verification guarantee.

---

## Audit status

Audit verdict **PASS** as of 2026-05-12 (assertion 9243 on
`todo:provenance-spec-phase-1-schema-registration`, `review_status=staged`,
`quality_score=0.88`).

In-tree producers all pass `resolved_config["model_id"]` — the
pipeline-resolved model alias, which is family/version-level for cloud
models and worker-alias-level for local models. Specifically verified:

- `services/universal-stargate/systems/pipeline/core/handlers/generate.py` (line 807)
- `services/universal-stargate/systems/pipeline/core/handlers/protocol.py` (line 101)
- `pipelines/consensus/v6.0/handlers/answer.py` (line 211)
- `pipelines/consensus/v7/handlers/answer.py` (line 211)
- `pipelines/consensus/v7.1/handlers/answer.py` (line 211)
- `pipelines/consensus/v8.0/handlers/answer.py` (line 211)

No seat or session decorators reach the comparison string in production.

---

## When to consult this skill

Trigger on any of the following:

1. Adding a new pipeline that creates provenance records — confirm the
   `model_id` source is `resolved_config["model_id"]` or equivalent
   family/version alias, not a dispatch-shape or seat decorator.
2. Refactoring producer code that touches the `seeded_by` / originator
   field on assertions or the `Provenance.originator_model_id` field.
3. Investigating an unexpected independence-gate result — first check
   whether the strings being compared carry seat/session decorators.
4. Reviewing Phase 5 (verification pass) failures — granularity
   mismatches manifest as false-positive "independent" results.
5. Cross-service refactors that change `resolved_config["model_id"]`
   semantics anywhere in the pipeline.

---

## v2 hardening (deferred, non-blocking)

The current contract is enforced by caller discipline. A future hardening
introduces `libs/provenance.normalize_model_identity()` that strips any
seat/session decorators before comparison, making the contract explicit
rather than implicit. Tracked under
`todo:provenance-normalize-model-identity` (not yet filed at time of
audit). Non-blocking — current production paths are clean.

---

## Related

- Spec `docs/architecture/entity-backed-claim-provenance.md` § 5.2,
  § 10.5
- Audit assertion `cortex://assertion/9243`
- Source `libs/provenance/assertions.py` `is_independent` docstring
  (contains the granularity rule inline)
- Agent skill `lawyer-stance` — adjacent
  reasoning posture for any legal-citation work that depends on
  cross-model verification
