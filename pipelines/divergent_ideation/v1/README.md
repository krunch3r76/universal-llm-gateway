# divergent-ideation-v1

Stargate map-pool pipeline for decontextualized operator injection and cross-family audit without winner selection.

## Step order

1. `load_problem` — parse problem JSON from pipeline source input; pool manifest from `sourceNs.metadata.pool_manifest`.
2. Decontextualize / leak-check pairs (`decontextualize_0` … `leak_check_2`) with terminal `leak_check_final`.
3. `retrieve_operators` — RAG scope `llm_prompting`.
4. `operator_split` — deterministic partition; emits `pool_models` for map pool binding.
5. `map_ideate` — map over operator subsets with `model_pool` + `selection: rotate`, `min_success_threshold: 0`, JSON object responses.
6. `re_instantiate` — map over `map_ideate.*` with per-item `model_id`.
7. `audit_assign` — exclusion authority for auditor pairs.
8. `map_audit` — one generate call per assignment item.
9. `collate` — run manifest, convergence metric, halt reasons.
10. `emit` — `select_output` of collate.

## Schemas

Typed dataclasses live in `libs/divergent_ideation/schemas.py` (P, A, L, O, B-abstract, B, X, U, M).

## Audit assignment

`audit_assign_v1` removes the author from the candidate pool, prefers cross-family auditors, and draws `min_auditors` distinct auditors per brief using `sha256(run_seed:brief_id:model)` ordering.

## Metric

`collate_v1` computes `convergence_rate` from cross-family audit labels; `falsified` when rate ≥ 0.50. `operator_dispersion` is computed in `operator_split_v1`.

## Benchmark assets

Under `benchmark/`: three schema-P problem documents and `pool_manifest.json` (qwen + gemma locals + cloud frontier).
