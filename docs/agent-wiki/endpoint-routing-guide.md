# Provider Endpoint Routing Guide

Machine-readable + agent-readable capability map for cross-provider routing.
All agents (Claude, Skeptic, Reviewer, Gatherer) should consult this when choosing
which generation tool and model to use for a given task.

## Tool Selection Quick Reference

| Tool | Provider | API Shape | When to Use |
|------|----------|-----------|-------------|
| `pipeline(chat-dispatch)` | Any | OpenAI Chat Completions (via chat-dispatch) | Generic text generation, any model in catalog, no provider-native features needed |
| `claude_generate` | Anthropic | Messages API | Thinking, extended thinking, document blocks, citations, speed mode, MCP connector |
| `grok_generate` | xAI | Responses API | x_search, conversation state, reasoning traces, 2M context, Skeptic role |
| `openai_generate` | OpenAI | Responses API | Reasoning effort, web search, code interpreter, file search, strict JSON schema |
| `agent_consult` | Multi (Grok + OpenAI) | Function calling loop | Advisory consultation with Cortex + RAG tool access, evidence triangulation |

## Task-to-Endpoint Matrix

### Deep Reasoning

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| Anthropic | `claude-opus-4` | `claude_generate` | Extended thinking with `budget_tokens`, highest quality reasoning |
| OpenAI | `o4-mini`, `o3` | `openai_generate` | Built-in reasoning, `reasoning_effort` control |
| xAI | `grok-4.20-reasoning` | `grok_generate` | 2M context, built-in reasoning |

### Web-Grounded Research

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| OpenAI | `gpt-5.4` / deep-research | `openai_generate` / `chat-dispatch` | Card lists `web_search_preview`; omit `server_tools` (= ALL card-derived). Do not pass a subset tool whitelist. |
| xAI | `grok-4.20-*` | `grok_generate` | Card lists `web_search`, `x_search`, `code_interpreter`; omit `server_tools` (= ALL). |
| Anthropic | `claude-sonnet-4` | `claude_generate` | Card has no server-side builtins on the inject path; MCP client tools via `mcp=`. |

### Code Execution (Sandboxed)

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| OpenAI | `gpt-5.4` | `openai_generate` | `code_interpreter` is opt-in via `provider_options.openai.tools` (not card-default). |
| xAI | `grok-4.20-*` | `grok_generate` | Included in card default (`code_interpreter`). |
| Anthropic | `claude-sonnet-4` | `claude_generate` | Via Messages `req.tools` + version map when caller supplies; not card-injected. |

### Structured Output (Schema-Locked JSON)

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| OpenAI | `gpt-5.4` | `openai_generate` | `response_format={"json_schema": {"name": "...", "schema": {...}, "strict": true}}` — strict mode guarantees schema conformance |
| Anthropic | `claude-sonnet-4` | `claude_generate` | `response_format={"type": "json_object"}` or tool-use with schema |
| xAI | `grok-4.20-*` | `grok_generate` | `response_format` — structured output support |

### Document Analysis (PDF)

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| Anthropic | `claude-sonnet-4` | `claude_generate` | **Document blocks** — native PDF input as base64 in message content, bypasses local extraction |
| Local | — | `fs(op="read")` | pymupdf4llm extraction (prose-oriented) |
| Local | — | `finance(op="inspect")` | pdfplumber extraction (tabular PDFs) |

### Multi-Agent Orchestration

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| xAI | `grok-4.20-multi-agent` | `grok_generate` | Skeptic role auto-loaded, multi-agent optimized |
| Anthropic | `claude-sonnet-4` | `claude_generate` | API Claude seed with boot=mcp, MCP connector injection |
| OpenAI | `gpt-5.4` | `openai_generate` | boot=none by default, MCP connector available |

### Fast / Low-Latency

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| Anthropic | `claude-sonnet-4` | `claude_generate` | `speed="fast"` — reduced latency tier |
| OpenAI | `gpt-5.4-nano` | `openai_generate` | Cheapest GPT-5.4 class |
| xAI | `grok-4-1-fast-reasoning` | `grok_generate` | Fast + cheap reasoning |

### Image Generation

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| OpenAI | `gpt-image-1` | Via Stargate cloud proxy | Image generation |
| xAI | `grok-imagine-image` | Via Stargate cloud proxy | Image generation |

### Vision / Image Understanding

| Provider | Model | Tool | Notes |
|----------|-------|------|-------|
| OpenAI | `gpt-5.4` | `openai_generate` | Image input in messages |
| Anthropic | `claude-sonnet-4` | `claude_generate` | Image input in messages |
| xAI | `grok-4.20-*` | `grok_generate` | Image understanding, OCR |

## Provider-Unique Capabilities

Features available on only one provider:

| Capability | Provider | Tool | Access |
|------------|----------|------|--------|
| `x_search` (real-time X/Twitter) | xAI | `grok_generate` | Card default includes `x_search`; omit `server_tools` |
| Document blocks (native PDF) | Anthropic | `claude_generate` | base64 PDF in message content |
| Extended thinking (`budget_tokens`) | Anthropic | `claude_generate` | `thinking={"type": "enabled", "budget_tokens": N}` |
| Speed mode | Anthropic | `claude_generate` | `speed="fast"` |
| Citations | Anthropic | `claude_generate` | Structured source attribution |
| Computer use beta | Anthropic | `claude_generate` | GUI interaction |
| `conversation_id` (stateful) | xAI | `grok_generate` | Multi-turn server-side state |
| `reasoning_trace` injection | xAI | `grok_generate` | Continue prior reasoning chain |
| Strict JSON schema mode | OpenAI | `openai_generate` | `response_format.json_schema.strict=true` |
| File search server tool | OpenAI | `openai_generate` | Opt-in via `provider_options.openai.tools` (needs vector stores; not card-default) |
| Native MCP server ingestion | OpenAI | `openai_generate` | Responses API MCP tool type |

## Cost Signals

| Tool | Approx. Cost | Notes |
|------|-------------|-------|
| xAI server tools | $5/1k calls | web_search, x_search, code_execution |
| Anthropic speed mode | Higher per-token | Reduced latency trade-off |
| OpenAI reasoning models | Higher per-token | o4-mini/o3 reasoning token costs |
| `gpt-5.4-nano` | Lowest OpenAI | High-volume simple tasks |
| `grok-4-1-fast-*` | Lowest xAI | Quick reasoning tasks |

## Boot / Persona Defaults

| Tool | Default Boot | Persona |
|------|-------------|---------|
| `grok_generate` (multi-agent) | `mcp` | Skeptic (auto-loaded) |
| `grok_generate` (other models) | `mcp` | Neutral |
| `claude_generate` | `mcp` | API Claude (auto-loaded) |
| `openai_generate` | `none` | No persona — use `boot_ref` for custom |
