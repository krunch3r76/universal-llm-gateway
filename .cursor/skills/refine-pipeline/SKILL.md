# Refine Pipeline

Procedural skill for iterating on **existing** pipelines. Complements the
build-pipeline skill (which gets to a *working* pipeline; this gets to a
*good* pipeline).

**Domain agnosticism (CRITICAL)**: Pipelines are generic infrastructure.
∀ fix (prompt, handler, config, vocabulary): the solution must improve the
pipeline's general capability, not overfit to the specific test queries.
If a fix only helps the 3 queries in the eval set but wouldn't generalize
to novel inputs, it's the wrong fix. Prefer structural/algorithmic
improvements (register priority, co-occurrence weighting, prompt
constraints) over query-specific patches (removing a vocabulary term
because it hurts one query, adding a rewrite that targets one topic).

**Prerequisites**: A pipeline that runs end-to-end. At least one execution
you can snapshot or trigger.

**Two workflow paths**:

| Context | Primary tools | When to use |
|---------|---------------|-------------|
| MCP-connected (Cursor agent, cloud agent) | `pipeline`, `observability`, `quality_gate` | Default — full closed loop without user intervention |
| Terminal / non-MCP | `tools/pipeline_test` CLI | Manual sessions, single-step replay |

MCP tools are preferred when available. CLI instructions are kept as fallback.

**If using CLI path**, activate venv once at the start:

```bash
source ~/.venvs/universal/bin/activate
```

---

## Step 1: Orient

Capture the execution state and understand what you're working with.

### MCP path

```
# Run the pipeline to get a fresh execution
result = pipeline(op="run", pipeline_id="{pipeline-id}", messages=[{"role": "user", "content": "test input"}])
→ {"content": "...", "execution_id": "abc123", "duration_s": 3.2}

# CRITICAL: pin the execution_id immediately
EXEC_ID = result["execution_id"]

# Get step-by-step trace (model selection, timing, pass/fail per step)
observability(operation="pipeline-trace", params={"execution_id": EXEC_ID})

# Check system health if something looks wrong
observability(operation="capacity-snapshot")
observability(operation="recent-failures")
```

### CLI path

```bash
python -m tools.pipeline_test list {pipeline-id}
python -m tools.pipeline_test snapshot --latest {pipeline-id}

EXEC_ID=$(python -m tools.pipeline_test list {pipeline-id} | head -3 | grep -oP '\b[0-9a-f]{8}\b' | head -1)
echo "Pinned to: $EXEC_ID"

python -m tools.pipeline_test refine-context fixtures/{pipeline}_{EXEC_ID}.json \
  -s {step_name} --summary
```

**Output**: You have an execution ID and know the step names, model
assignments, timing, and pass/fail status. All subsequent commands use
the pinned execution ID, never `--latest`.

---

## Step 2: Diagnose

Narrow in on the problem step using progressive context.

### MCP path

The `pipeline-trace` from Step 1 shows per-step results. For deeper diagnosis:

```
# Check if the issue is model-related (wrong model, slow, OOM)
observability(operation="model-timeline", params={"model_id": "<model from trace>"})

# Check for recent failures across the system
observability(operation="recent-failures", params={"limit": 10})

# Read the pipeline's prompt templates to understand what the model received
# (use MCP tool: fs(sandbox="project", op="read", path=...))
```

### Querying custom handler event payloads (MCP)

Custom handlers that publish bus events (e.g., RefineGenerationContextHandler)
emit signals like `pipeline.rag.generation.context.refined`. To inspect the
payload for a specific execution:

```
# All refine context events for an execution
observability(operation="signal-events", params={
    "signal": "pipeline.rag.generation.context.refined",
    "execution_id": EXEC_ID,
    "limit": 5
})

# Glob pattern — all rag-related signals
observability(operation="signal-events", params={
    "signal": "pipeline.rag.*",
    "limit": 10
})

# The payload contains handler-specific fields (e.g., scope_anchors_added,
# enriched_must_include, flat_hint_count). Use this to verify data
# transformations without needing pipeline_test snapshot + fixture inspection.
```

### Events table schema (for raw_sql)

Columns: seq, event_id, signal, role, scope, ts_unix_ms, timestamp,
         source, request_id, execution_id, model_id, gateway_id, payload

payload is a JSON string. Use json_extract() or parse client-side.

```
# Parameterized raw SQL (correct form)
observability(operation="raw_sql", params={
    "sql": "SELECT signal, payload FROM events WHERE execution_id = ? AND signal LIKE 'pipeline.rag%' ORDER BY seq DESC",
    "params": ["<execution_id>"],
    "limit": 20
})
```

### CLI path

```bash
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} --prompt

python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} --output

# If assess_loop step, check a specific call
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} -c assess_0 --prompt
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} -c assess_0 --output
```

**Identify the failure mode** before changing anything:

| Failure mode | Symptoms | Likely cause |
|---|---|---|
| Wrong format | JSON parse errors, missing fields | Prompt lacks format constraints or examples |
| Hallucination | Fabricated facts, invented file paths | Missing context, no grounding instruction |
| Verbosity | Output bloated with filler | No length constraint, encourages elaboration |
| Missed instruction | Ignores a rule in the prompt | Rule buried in prose, not in a constraints list |
| Refusal | Model declines the task | Safety trigger, reframe the instruction |
| Incoherence | Output contradicts itself | Multi-objective prompt, model overwhelmed |

---

## Step 3: Consult

Get expert advice on the problem. Three paths depending on context.

**Model preference**: Prefer cloud models for consultation when the cloud proxy
is available. Cloud models produce substantially better prompt-engineering advice
than local models. Until a curated list of consultation-capable local models
exists, treat cloud as the default for this step.

### MCP path (preferred)

Use the pinned execution trace plus RAG research for step-specific advice:

```
# Step metadata (model, timing, tokens) — re-fetch if Step 1 trace is stale
observability(operation="pipeline-trace", params={"execution_id": EXEC_ID})

# Grounded research for the failure mode (include prompt excerpt + bad output)
rag(op="search", arguments='{"query": "techniques for structured JSON output in 7B models", "scope": "research_small_llm", "top_k": 10}')
rag(op="answer", arguments='{"query": "Prompt: {paste}. Output: {paste}. Problem: redundant paragraphs, ignores length constraint.", "scope": "research_small_llm"}')
```

Read prompt templates via `fs(sandbox="workspaces", op="read", path=...)`. Include
as much context as possible — prompt text, model output excerpt, and what
specifically is wrong.

**Scope auto-detection** (override in RAG `arguments` when needed):
- Cloud model (`/` in model ID) → `research`
- Local model → `research_small_llm`
- Unknown → `research` (broader coverage)

Optional overflow (not required): `dispatch(tool="pipeline_consult", arguments='{"execution_id": "...", "step_name": "...", "problem": "..."}')` when trace + RAG is insufficient.

For broader research not tied to a specific step, use `rag(op="search", ...)` or `rag(op="answer", ...)` directly:

```
rag(op="search", arguments='{"query": "techniques for structured JSON output in 7B models", "scope": "research_small_llm", "top_k": 10}')
```

To discover all available scopes at runtime:

```
rag(op="list_scopes")
→ {"scopes": ["research", "research_small_llm", "workflows", ...], "details": {...}}
```

### pipeline_test consult (step-specific, CLI)

```bash
# Default: chained, scope auto-detected from model tier
# --cloud-only recommended — cloud models give better consultation results
python -m tools.pipeline_test consult fixtures/{FILE}.json \
  -s {step_name} -p "the output has redundant paragraphs" --cloud-only

# For assess_loop steps, target a specific call
python -m tools.pipeline_test consult fixtures/{FILE}.json \
  -s {step_name} -c assess_0 -p "critique misses duplicate logic" --cloud-only
```

**Scope auto-detection**:
- Cloud model (`/` in ID) → `research` scope (large-model research)
- Local model → `research_small_llm` scope (small-model research)

**Override when needed**:

| Override | When |
|---|---|
| `--scope research` | Mixed-tier pipeline, want broader research coverage |
| `--scope workflows` | Problem is about pipeline topology, not the prompt |
| `--no-rag` | Problem is purely about output format, research won't help |

### scripts/consult (design questions)

```bash
# Pipeline structure questions (--cloud-only for best advice quality)
scripts/consult -r architect --scope workflows --cloud-only \
  -f pipelines/{domain}/ \
  "Should this be a sub-pipeline or inline steps?"

# Prompt strategy questions (not tied to a specific step)
scripts/consult -r researcher --scope research --cloud-only \
  "What prompting techniques improve JSON schema compliance for 7B models?"
```

### Interpreting consultant output

The `prompt_engineer` role classifies root causes into three categories:

| Root cause | Action |
|---|---|
| **Prompt issue** | Edit the prompt (Step 4). This is the common case. |
| **Model capability issue** | Change `model_ref` in sandbox YAML, not the prompt. |
| **System issue** | Investigate RAG context, handler logic, or schema constraints. |

---

## Step 4: Fix

Create a sandbox and apply the recommended changes.

```bash
# Create sandbox (copies pipeline YAML to /tmp)
python -m tools.pipeline_test sandbox create pipelines/{domain}/{version}

# Edit the prompt in the sandbox
# (use your editor or StrReplace on the sandbox file)
# Sandbox location: /tmp/pipeline_sandboxes/{domain}-{version}/prompts.yaml
```

**Prompt editing principles** (exhaust these before changing models):

1. **Domain agnostic** — ¬reference test queries, specific topics, or named
   entities from the eval set. Fixes must generalize to unseen inputs.
2. **Shorter is often better** — terse instructions > verbose explanations for small models
3. **Constraints as a list** — bullet points at the top, not buried in prose
4. **One objective per prompt** — split multi-objective prompts into separate steps
5. **NLP terminology** — "coreference resolution", "semantic overlap", "discourse coherence"
   can be more precise than natural language descriptions
6. **Remove anchoring examples** — examples that demonstrate the wrong pattern get followed

For model changes, edit `models.yaml` or the step's `model_ref` in the sandbox
chain YAML. For generation parameter changes, edit `generation_parameters` in the
step config.

---

## Step 5: Verify

Re-run and compare against the original.

### MCP path

```
# If handler .py files were modified: rebuild gateway, then wait for healthy
# manage(action="rebuild", service="gateway")
# manage(action="wait_healthy", service="gateway", timeout=120)

# Re-run with the same input as the baseline
result_b = pipeline(op="run", pipeline_id="{pipeline-id}", messages=[{"role": "user", "content": "same test input"}])

# Compare the two runs side-by-side (latency, step diffs, model changes)
observability(operation="compare-runs", params={"run_a": EXEC_ID, "run_b": result_b["execution_id"]})

# If handler code was changed, verify quality
quality_gate(files=["pipelines/{domain}/v1/handlers/my_handler.py"])
```

### CLI path

```bash
python -m tools.pipeline_test replay fixtures/{FILE}.json \
  -s {step_name} \
  --pipeline-dir /tmp/pipeline_sandboxes/{domain}-{version} \
  -o replay.json

python -m tools.pipeline_test compare fixtures/{FILE}.json \
  replay.json -s {step_name}
```

**Evaluate the diff**:

| Outcome | Next action |
|---|---|
| Problem fixed, no regressions | Proceed to Step 6 (Apply) |
| Problem fixed, but output structure changed | Check downstream steps — do they break? |
| Partially improved | Try another prompt variant (back to Step 4) |
| No improvement after 2+ prompt variants | Consult again or try a different model |
| Worse than original | Revert sandbox changes, rethink approach |

**Downstream check** (only when output structure changed):

```bash
# Inspect the downstream step to see if it receives the reshaped output
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {downstream_step} --summary
```

---

## Step 6: Apply

When satisfied, apply changes and run a final verification.

### MCP path

Changes were already made to the repo files in Step 4. Run final validation:

```
# Validate pipeline structure
pipeline(op="validate", pipeline_id="{pipeline-id}")
→ {"valid": true, "pipeline": "{pipeline-id}", "steps": N, "models": [...], "errors": []}

# Full end-to-end run
final = pipeline(op="run", pipeline_id="{pipeline-id}", messages=[{"role": "user", "content": "test input"}])

# Compare against original baseline
observability(operation="compare-runs", params={"run_a": EXEC_ID, "run_b": final["execution_id"]})

# Quality gate on any modified handler code
quality_gate(files=["pipelines/{domain}/v1/handlers/my_handler.py"])
```

### CLI path

```bash
python -m tools.pipeline_test sandbox apply {sandbox-name} pipelines/{domain}/{version}
python -m tools.pipeline_test sandbox clean {sandbox-name}

curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "{pipeline-id}", "messages": [{"role": "user", "content": "test input"}]}'
```

Snapshot the new execution and compare key steps against the pre-refinement
fixture to confirm the improvement persists end-to-end.

---

## Common Patterns

### Iterating on assess_loop steps

Assess loops have multiple calls (assess_0, assess_1, ...). Focus on the
**first failed assessment** — the critic's rejection reason tells you what
the generator got wrong.

```bash
# Find which call was rejected
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} --summary

# Read the critic's rejection
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} -c assess_0 --output

# Read the generator's attempt that was rejected
python -m tools.pipeline_test refine-context fixtures/{FILE}.json \
  -s {step_name} -c generate_0 --output
```

### Model A/B testing

Use `--model` to replay with a different model without editing YAML:

```bash
python -m tools.pipeline_test replay fixtures/{FILE}.json \
  -s {step_name} --model {alternative-model-id} -o replay-alt.json

python -m tools.pipeline_test compare fixtures/{FILE}.json \
  replay-alt.json -s {step_name}
```

### Multi-step refinement

When changes to one step affect downstream steps, work **upstream to
downstream**. Pin to the same fixture throughout. Only expand scope when
replay evidence shows downstream impact — not speculatively.

---

## Escalation

Stop and involve the user when:

- Two substantively different prompt variants show no improvement
- Consultant identifies a **model capability issue** and model change is needed
- The problem is in handler logic, not prompts (system issue)
- Changes would alter the pipeline's step structure (add/remove/reorder steps)
- Three refinement cycles with no convergence

With MCP tools, agents can run more iterations autonomously before escalating.
Use `compare-runs` evidence to justify the escalation: "tried 3 prompt variants,
latency improved but output quality degraded in all runs — here's the comparison."

---

## Related cortex skills

- skill `implementation-plan-workflow` — when a refinement cycle is one phase of a multi-phase plan deck (e.g. `tmp/prompts/{name}/phase-N-refine-{pipeline-id}.md`). The phase-doc contract still applies: BEFORE/AFTER blocks for sandbox-staged prompt edits, verification via `compare-runs`.
- skill `ulg-architecture` § Event Service Primary — for diagnosing pipeline behavior via the event service when this skill's `signal-events` queries surface anomalies that point at infrastructure rather than prompt issues.
- `.cursor/skills/build-pipeline/SKILL.md` (sibling workspace skill) — when refinement reveals the need for a new pipeline version or sub-pipeline restructure rather than a prompt fix.
