# Binary suffix census — Cursor rules (2026-07-18)

**Axis:** `_ulg` = shared across ULG ecosystem · `_ws` = this checkout only.
Supersedes U/E/L three-way for **filenames**. Entity-twin `_ulg` IDs remain retired.

Machine-readable list: `RULES_ULG_CENSUS.txt` (assembled into plugin `rules/`).

**AlwaysApply rules thinning (2026-09-01, agent-bus:9848):** 31 → 15 always-on files across
the three SoT trees (plugin/hub/parent), verified by `scripts/cursor/alwaysapply_rules_census.py`
(32649 → 7554 tokens, chars/4). `judgment-escalation-ladder_ulg`, `lean-context-dispatch-first_ulg`,
`bind-then-compose-dispatch_ulg`, `operator-request-front-door_ulg`, `recon-default_ulg`,
`anthropic-substrate_ulg` merged into `dispatch-kernel_ulg`; `dispatch-in-flight-supremacy_ulg`,
`session-abort-authorization_ulg` merged into `in-flight-work-guard_ulg`; `commit-and-git-scope_ulg`,
`shared-checkout-housekeeping_ulg` merged into `checkout-kernel_ulg`; `phase-vocabulary_ulg` retired
(boot-card + `entity-lifecycle-discipline` already own the vocabulary). Detail install/upload playbook
split off `skill-surface_ulg` into `skill-surface-sync_ulg` (`alwaysApply: false`). Compressed-out
detail relocated to `runbook:restart-drain`, `consult-routing` skill § Judgment escalation ladder /
§ Non-primary model gate, and bind-history assertions on the governing `decision:` entities — see
`cortex://notes/system/threads/alwaysapply-rules-thinning-g1-adjudication.md`.

**Authoring discovery:** `cursor-rule-placement_ulg.mdc` — description-gated; triggers when
creating/editing ecosystem-shared rules. Parent catalog: `index.mdc`.

## Remain `_ws` in ULG (hub / checkout-local)

core_ws, cloud-model-routing_ws, model-catalog-ids_ws, federation_ws, topology_ws,
routing_ws, pipeline_*, stargate-live-state_ws, services_ws, event-debugging_ws,
rag_ws, cortex-registry_ws, cortex-workbench_ws, cursor-boot_ws, cursor-environment_ws,
agent-skills_ws, mcp-integration_ws, arch-docs-maintenance_ws, commit_ws, doc-check_ws,
doc-patterns_ws, docs-write-guard_ws, journal-entry_ws, insight_ws, vision_ws,
check-doc-opportunities_ws, consensus_metrics_ws, frontier-model-context_ws,
json_schema_ws, yaml-naming_ws, testing_ws, modularize_ws, orion-feedback-discipline_ws,
session-transcript-fidelity_ws, and satellite `core_ws.mdc` files.
