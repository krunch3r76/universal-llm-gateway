# Model-Add Ownership Checklist

**Scope**: Adding a cloud model (or a new family) to the `capability_dispatch` registry.

This checklist is a **gate, not advisory**.  Every step must be satisfied before a
model-add is complete.  Steps 3–5 are the live-truth gate the offline lane cannot give.

---

## Steps

### 1. Add the registry row(s)

In `registry.py`:

- `_ANTHROPIC_MAX_OUTPUT_CEILINGS` entry (ceiling, ordered most-specific first) — Anthropic only.
- `_ANTHROPIC_ADAPTIVE_FAMILIES` membership — if the model supports adaptive thinking.
- For new non-Anthropic surfaces: verify `_PROVIDER_SURFACE` covers the provider.
- Ensure `_build_max_output` and `_build_reasoning` produce the correct `CapabilityDispatch`
  for the new model family.

Fields to confirm: `ceiling` / `floor` / `default` / `native_field`, reasoning `value_kind`
(+ `budget_map` if `token_budget`), `api_surface`.

### 2. Lane A — offline tests (every PR)

Run and confirm green **before** merging:

```bash
pytest libs/llm_adapters/test_dispatch_registry_coherence.py \
       libs/llm_adapters/test_max_output_parity.py -q
```

**New family** ⇒ extend the parity matrix in `test_max_output_parity.py` and
re-freeze the golden table deliberately:

```bash
# Delete the existing baseline to force a re-freeze on next run.
rm libs/llm_adapters/_max_output_parity_baseline.json
pytest libs/llm_adapters/test_max_output_parity.py -q   # re-captures baseline
```

> Re-freezing the baseline is an **explicit, reviewed act** — the baseline is a
> reviewed contract.  Never silently update it; always review the diff before commit.

### 3. Lane B.1 — declarative diff

Run the declarative probe for the new model's provider:

```bash
python scripts/dispatch-anti-drift/run.py --probe declarative
```

- **Google `models.get`**: verify `registry_ceiling` matches `declared_ceiling`.
- **Anthropic `/v1/models`** (if the endpoint carries `max_tokens`): verify ceiling match.
- **OpenAI / xAI**: tier-1 N/A — ground truth comes from Lane B.2.

If there is a discrepancy, record the reason in the Cortex assertion (step 6).

### 4. Lane B.2 — behavioral probe

Run the behavioral probe against the live Stargate:

```bash
python scripts/dispatch-anti-drift/run.py --probe behavioral --live
```

Confirm: resolved values are accepted by the provider as the registry predicts
(floor-bump, ceiling-clamp, cross-knob bump, reasoning-effort acceptance).

### 5. Lane B.3 — tool-loop fidelity

Run the tool-loop probe for the new model:

```bash
python scripts/dispatch-anti-drift/run.py --probe toolloop --models provider/model-id
```

Confirm:
- `tool_executed ≥ 1` for Task A.
- `tool_executed ≥ 2` for Task B.
- No `UNEXPECTED_TOOL_CALL` or `MALFORMED_FUNCTION_CALL` regression class.
- Loop terminates correctly.

Collect the `execution_id` values from the JSON report — required for step 6.

### 6. Record a confirmed Cortex assertion

Seed a `confirmed` assertion on `decision:model-dispatch-max-output-facet` (or the
model entity) with the Lane B execution IDs as `evidence_uris`:

```python
cortex(tool="assert", arguments={
    "entity_id": "decision:model-dispatch-max-output-facet",
    "claim": "model-id added; Lane B.1/B.2/B.3 passed. execution_ids: [...]",
    "confidence": "confirmed",
    "evidence": "Lane B probes passed on YYYY-MM-DD",
    "evidence_uris": ["execution:<id-a>", "execution:<id-b>"],
    "derivation_type": "inference",
    "confidence_score": 0.99,
})
```

### 7. Docs

- If any event or contract changed: audit `docs/event-contracts.md` and update.
- If the registry module's enumerated responsibilities changed: refresh the
  `registry.py` module docstring.
- Update this checklist if the probe matrix or registry shape changes.

---

## Quick-reference: probe commands

```bash
# Full Lane B run (requires live credentials + Stargate)
python scripts/dispatch-anti-drift/run.py --probe all --live

# Targeted model-add run
python scripts/dispatch-anti-drift/run.py --probe toolloop --models anthropic/claude-new-model

# Lane A only (offline, no credentials needed)
pytest libs/llm_adapters/test_dispatch_registry_coherence.py \
       libs/llm_adapters/test_max_output_parity.py -q
```

---

*Checklist version: G2 anti-drift CI fast-follow (thread 1310 design SoT §7).*
