<!-- target:* -->
# Frontier Model Context Policy

## Invariant

**Invariant**: ∀ frontier/cloud/OpenRouter model invocations: ¬impose arbitrary
`max_tokens` limits. Let the model choose its output length unless the task has
a structural constraint (e.g. JSON schema with bounded fields).

## Rules

- Cloud models (Anthropic, OpenAI, OpenRouter) have large context windows.
  Pipeline steps SHOULD NOT set restrictive `max_tokens` unless the expected
  output has a known bounded size.
- When `max_tokens` is needed (e.g. for cost control), use generous defaults:
  - Classification/structured output: 4096–8192
  - Free-form generation: 16384+
  - Summarization/analysis: 8192+
- Local models may need tighter limits for VRAM/throughput reasons — this
  policy applies to frontier/cloud models only.
- ∀ pipeline YAML `generation_parameters.max_tokens`: document why the limit
  exists if it's below 8192 for a cloud model step.

## Anti-Patterns

| Bad | Good |
|---|---|
| `max_tokens: 512` for classification with 27 scopes | `max_tokens: 8192` or omit |
| Same `max_tokens` for local and frontier modes | Mode-aware defaults |
| Hardcoded limits without considering output structure | Limits derived from expected output size |
<!-- /target:* -->
