---
name: pipeline-substrate-capabilities
description: "When designing or modifying Stargate pipelines — topology, steps, conditions, response_format, and substrate capability facts for correct design rulings."
trigger_match_terms: ["pipeline-substrate-capabilities", "pipeline_substrate_capabilities", "design", "modify", "stargate", "pipeline", "pipelines-rag-mcp", "designs", "modifies", "reasons", "topology", "steps"]
---

# Pipeline Substrate Capabilities

**Version:** 1.0
**Created:** 2026-06-01
**Authority level:** HIGH — capability facts gate the correctness of pipeline design rulings.

## When to read this skill

Read before forming findings or asserting engine behavior when the task involves:
- Designing or modifying a Stargate pipeline — topology, steps, `condition`, `response_format` schemas, `model_ref`/`prompt_ref`.
- Judging whether an ingest path (e.g. email-bridge) can drive a given pipeline type — frozen-schema vs agentic.
- Reasoning about pipeline registration, model-catalog gating, or step skip/selection behavior.

`reasoning_about_pipeline_engine ∧ ¬read_canonical_reference ⇒ confident_wrong` — the failure this skill exists to prevent (three capability errors, thread 1166, web-anthropic seat).

## Core rule

The single source of truth is the **build-pipeline** skill in the universal-llm-gateway repo. Read it first; do not re-derive pipeline capabilities from memory.

- `workspaces://universal-llm-gateway/.cursor/skills/build-pipeline/SKILL.md` — golden path, primitive decision tree, binding namespaces, critical invariants, error triage.
- `.../build-pipeline/yaml-reference.md` — schema v6: step config, `condition`, `model_ref`/`prompt_ref` as static per-step aliases, `optionsNs`, map config, sub-pipeline fragments. Source: `pipeline_config.py` / `step_config.py`.
- `.../build-pipeline/handler-reference.md` — `BaseHandler`, `StepOutput`, `PipelineContext` (`get_output`, `get_option`/`runtime_options` ← HTTP `pipeline_options`), built-in step types incl. `select_output` (pick first non-skipped output).

`seat ∈ {cursor, gpt-cursor} ⇒ build-pipeline auto-loads from .cursor/`; `seat ∈ {web-anthropic, grok-web, subagent} ⇒ ¬auto-load ⇒ read via this pointer`. `agent_skill:ulg-architecture` (the ULG-repo architecture skill) is `applicable_agents: [cursor, web-anthropic]` (assertion 22836) and now loads on the web seat too; this skill remains the web seat's pipeline-capability pointer into build-pipeline.

## Companion skills

- `architecture-invariants` (discipline) — load alongside for architectural posture (no-BC, thin handlers, libs-first). It carries no pipeline-capability facts; this skill supplies the capability pointer.

## Liveness caveat

`capability_claim ∧ load_bearing ⇒ verify_against_running_engine` before asserting. "Landed in tree" ≠ "live in the running registry": confirm via a real probe (PipelineRegistry / `/v1/models` / a request), not the YAML on disk. A stale capabilities card is confident-wrong.

## Runtime gotchas — pending upstream into build-pipeline (source: thread 1166; decision:email-extract-profile-redesign, assertion 11665)

1. **Skip-sentinel silent-empty hazard.** A condition-skipped step emits `StepOutput(json={"_skipped": True})` — a non-None dict. A downstream handler doing `_try_parse_json(get_output(step))` then guarding `is None` passes the sentinel through, reads `.get("entities", [])` → `[]`, and reports a 0-result success with `validation_errors: []`. Silent empty, worse than a surfaced parse error. Remedy: explicit `_is_skipped` guard that selects the live step and hard-errors on both-skipped; or the built-in `select_output` step type.
2. **Agentic MCP-loop reachability + output-contract blocker.** The MCP tool loop is a pipeline-engine capability: a `frontier_dispatch_v1` step → `run_native_tool_loop` (`options.mcp: true`), reachable through the same headless `/v1/chat/completions` + model-id entry the email-bridge already uses (live precedent: `cortex-chat-openai`). So a bridge needs zero changes to drive an agentic extract — but `frontier_dispatch_v1` returns free-form terminal content, not GBNF-constrained JSON, which breaks the bridge's stage→hash→commit + `filter_claims` contract. Surface available; clean frozen-schema integration is not.

## Minimal operating summary

- Single source of truth: build-pipeline (3 files, workspaces). Read before asserting engine behavior.
- `model_ref`/`prompt_ref` are static per-step aliases (name by capability role, not vendor); `condition` reads the namespaces incl. `optionsNs`; skipped steps emit `{_skipped: True}` — guard it.
- Verify load-bearing capability claims against the running engine, not the tree.
- The two gotchas above are pending upstream into build-pipeline; trim this section to a pointer once folded.
