<!-- target:* -->
# Cloud Model Routing

## Invariant

**Invariant**: ∀ inference requests (agent, script, pipeline): route through the gateway's Stargate surface. ¬direct cloud proxy, ¬direct provider API.

Per-model generation knobs (max_output floor/ceiling, reasoning effort, native fields):
see the shared capability-dispatch reference.

## Model ID Formats on Stargate

| Format | Provider routing | Example |
|---|---|---|
| `openrouter/provider/model` | OpenRouter relay | `openrouter/anthropic/claude-sonnet-4` |
| `anthropic/model` | Direct Anthropic API | `anthropic/claude-sonnet-4-6` |
| `xai/model` | Direct xAI API | `xai/grok-4.5` |
| `openai/model` | Direct OpenAI API | `openai/gpt-5.4` |
| `google/model` | Direct Google API | `google/gemini-3.1-pro-preview` |
| `{local-model-id}` | Local gateway (no `/`) | `hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid` |

## Stargate Surfaces

### OpenAI-compatible (all models)

All model IDs — local and cloud — are routable via the standard OpenAI surface:

```
POST :9999/v1/chat/completions   {"model": "openrouter/anthropic/claude-sonnet-4", ...}
GET  :9999/v1/models             # lists activated local + cloud models
```

### Local one-shot default — `?pseudostream=true`

`∀` agent/script/`curl` to a **local** model ID (bare, no `/`): default
`POST :9999/v1/chat/completions?pseudostream=true` with body `stream` false/omitted.
Forces upstream SSE, accumulates on master, returns JSON + `X-ULG-Pseudostream*` headers.
**Never** on cloud/`openrouter/` IDs or pipeline models. True SSE clients use body
`stream: true` without the query. Full playbook: skill `local-chat-completions`.

### Provider-native (direct Anthropic, xAI, OpenAI, Google)

For native request/response formats (non-OpenAI body shape):

```
POST :9999/api/v1/providers/anthropic/messages          # Anthropic Messages API body
POST :9999/api/v1/providers/xai/responses                # xAI Responses API body
POST :9999/api/v1/providers/openai/responses              # OpenAI Responses API body
POST :9999/api/v1/providers/google/generateContent        # Gemini generateContent body
```

### Cloud catalog & selection

```
GET  :9999/api/models       # full cloud proxy catalog
POST :9999/api/select       # model selection by tags/context
POST :9999/api/refresh      # force catalog re-fetch
GET  :9999/cloud-ui         # browser model browser
```

## Provider Preference

**Invariant**: ∀ Anthropic and xAI models: prefer direct provider routing (`anthropic/`, `xai/`) over `openrouter/` wrapping.

| Provider | Preferred | Fallback |
|---|---|---|
| Anthropic | `anthropic/claude-sonnet-4-6` | `openrouter/anthropic/claude-sonnet-4` |
| xAI | `xai/grok-4.5` | `openrouter/x-ai/grok-4.5` |
| OpenAI | latest capable model for automated dispatch / MCP writes; a slightly cheaper default for casual use | `openrouter/openai/gpt-4o` |
| Google | `google/gemini-3.6-flash` | `openrouter/google/gemini-3.6-flash` |
| Other (Qwen, Mistral, etc.) | `openrouter/qwen/qwen3-32b` | (no direct route) |

Direct routing avoids the OpenRouter middleman — lower latency, native features, no markup.
Use `openrouter/` only for providers without direct API integration, or as fallback.

## Agent Script Routing

∀ agent consult scripts, pipeline calls, MCP tool inference, and dispatch calls:
- Model IDs go to the gateway's Stargate endpoint via `/v1/chat/completions`
- MCP client tools are governed by the single `mcp` boolean (default on for tool-capable families; `false` forces inline-only). Remote-connector vs client-side-loop selection is internal and card-derived — not a caller parameter. Server-side provider built-ins are governed independently by the optional `server_tools` knob (omit = ALL; `false` suppresses card-derived built-ins). Agents should not reason about injection details.
- ¬`curl` to cloud proxy UDS directly
- ¬ direct HTTP client to a provider's public API base URL — the gateway owns routing

## Anti-Patterns

| Bad | Good |
|---|---|
| `openrouter/anthropic/claude-sonnet-4` for Anthropic | `anthropic/claude-sonnet-4-6` |
| `openrouter/x-ai/grok-4.5` for xAI | `xai/grok-4.5` |
| Calling cloud proxy UDS directly for inference | `POST :9999/v1/chat/completions` |
| Hardcoding provider API URLs in scripts | Route through the gateway |
<!-- /target:* -->
