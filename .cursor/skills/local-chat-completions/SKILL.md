---
name: local-chat-completions
description: "When calling Stargate /v1/chat/completions for local (bare, no-slash) models — default ?pseudostream=true, reasoning-off, reject cloud/pipeline misuse."
trigger_match_terms: ["local-chat-completions", "pseudostream", "local model", "hermes", "chat/completions", "stargate local", "one-shot local"]
---

# Local Chat Completions

## Trigger

Before any agent/script/`curl`/`httpx` call to `:9999/v1/chat/completions` where `model` is a **local** ID (no `/` — e.g. `hermes-3-…-hybrid`).

## Default query

`∀ local one-shot CC: URL includes ?pseudostream=true` unless the caller **must** consume raw SSE (`stream: true` body) or is probing non-stream behavior.

| Target | Query | Body |
|---|---|---|
| Local one-shot (JSON reply) | `?pseudostream=true` (+ `disable_profile=true` when bypassing profile) | `stream` false/omitted |
| True SSE client | omit `pseudostream` | `stream: true` |
| Cloud / OpenRouter (`provider/…`) | **never** `pseudostream` | normal |
| Pipeline model IDs | **never** `pseudostream` | pipeline contract |

Contract (live): upstream forced to SSE; master accumulates; client gets JSON + headers `X-ULG-Pseudostream*`. Rejects cloud, pipelines, and `stream:true`+`pseudostream`.

## Example

```bash
curl -sS 'http://localhost:9999/v1/chat/completions?pseudostream=true&disable_profile=true' \
  -H 'Content-Type: application/json' \
  -d '{"model":"hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid","messages":[{"role":"user","content":"…"}],"max_tokens":512,"temperature":0.25,"reasoning":{"effort":"none"}}'
```

## Companion knobs

- Reasoning-off: body `reasoning: {effort: none}` (local maps → `enable_thinking=false`).
- Observability: prefer `X-ULG-Pseudostream-Delta-Parts` > 0 as proof of true multi-delta upstream SSE.
- Routing invariant (all models): `cloud-model-routing` — always Stargate `:9999`.

## Anti-patterns

| Bad | Good |
|---|---|
| Local CC without `pseudostream` “because JSON is fine” | Default `?pseudostream=true` |
| `pseudostream` on `openrouter/…` / `anthropic/…` | Omit; cloud rejects |
| Hand-rolled SSE accumulate for local JSON needs | Gateway `pseudostream` |
| Holding `AwaitShell` for multi-minute local 70B | Background shell; end-turn / completion notify — see presence-discipline P4 |
