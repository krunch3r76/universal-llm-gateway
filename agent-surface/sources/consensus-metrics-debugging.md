<!-- target:* -->
# Consensus Metrics Debugging

**Metrics-first debugging for consensus pipeline synthesis quality.**

## Primary Signals (Read First)

Primary signals (outline-first synthesis):

1. `pipeline.consensus.organize.completed`
   - `total_facts`, `sections_created`, `facts_assigned`, `valid_json`
   - Outline structural quality + assignment completeness

2. `pipeline.step.completed` for `synthesize__organize_facts`
   - `duration_seconds`, token counts for outline creation latency/cost

3. `pipeline.assess_loop.started` / `pipeline.assess_loop.iteration_completed` / `pipeline.assess_loop.completed` for `synthesize__review_outline`
   - Judge loop iterations and actions taken

4. `pipeline.step.completed` for `synthesize__synthesize_from_outline`
   - `duration_seconds`, token counts for prose synthesis

5. `pipeline.consensus.coverage.completed`
   - `total_facts`, `covered_count`, `uncovered_count`, `mean_score`, `coverage_pct`, `threshold`
   - Canonical semantic-coverage audit metric on the live event bus

## Required Investigation Order

∀ consensus synthesis issue:
1. Read pipeline events first (the pipeline events JSONL)
2. If needed, read recorder JSONL events second (pipeline summaries log tree)
3. Only then inspect rendered answer text

## Quick Queries

```bash
# Full synthesis trace for latest execution
jq -c 'select(.signal | test("step.(started|completed)|assess_loop|coverage|organize")) | {signal, step: .payload.step_name, dur: .payload.duration_seconds, calls: .payload.model_call_count, valid_json: .payload.valid_json}' /tmp/pipeline-events/current.jsonl | tail -20

# Outline review loop details
jq -c 'select(.signal | test("assess_loop")) | {signal, step: .payload.step_name, action: .payload.action, iteration: .payload.iteration}' /tmp/pipeline-events/current.jsonl

# Coverage check
jq -c 'select(.signal == "pipeline.consensus.coverage.completed")' /tmp/pipeline-events/current.jsonl | tail -1
```

Legacy signals (disabled steps — only appear if re-enabled):
- `pipeline.consensus.combine.completed`

## Interpretation Rules

- `pipeline.consensus.coverage.completed.coverage_pct` compares against **post-synergy** facts
- High assess-loop iteration count with repeated `revise` suggests outline-organization instability
- High organize/synthesize duration with low coverage indicates structure-to-prose loss
<!-- /target:* -->
