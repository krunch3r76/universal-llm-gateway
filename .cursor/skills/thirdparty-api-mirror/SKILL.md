---
name: thirdparty-api-mirror
description: "When mirroring third-party API docs (xAI, OpenAI, Anthropic, MCP, etc.) into docs/thirdparty/ for local RAG without outbound vendor calls."
trigger_match_terms: ["thirdparty-api-mirror", "thirdparty_api_mirror", "mirror", "vendor", "api", "doc", "set", "rag", "tooling-observability", "request", "xai", "openai"]
---

# Third-party API mirror

Local mirrors live under `docs/thirdparty/{provider}/` so RAG can answer integration questions without outbound calls or blocked vendor sites. RAG watcher auto-indexes `docs/thirdparty/`; per-provider scopes (`xai_api`, `claude_api`, `openai_api`, `mcp`, `lighter`, ...) live in `~/.gateway/rag.yaml` with `vocab_mode: none`.

Full policy: `docs/thirdparty/mirror-policy.md`.

## Directory shape

```text
docs/thirdparty/{provider}/
  README.md
  refresh.md
  upstream/     # one vendor URL → one .md; exact source of truth
  summaries/    # curated multi-page distillations
  product/      # marketing/concept pages; excluded until clean markdown
```

| Tier | RAG use |
|---|---|
| `upstream/` | default retrieval target |
| `summaries/` | fallback when upstream missing/verbose |
| `product/` | exclude from RAG until re-rendered |

## Frontmatter contract

Every content `.md` under `upstream/` and `summaries/` starts with YAML:

```markdown
---
thirdparty_source_url: https://docs.x.ai/developers/tools/web-search
thirdparty_refreshed: 2026-04-26
thirdparty_title: "Web Search Tool"
thirdparty_derived_from:
  - upstream/tools/overview.md
---
```

Rules:
- `upstream/`: `thirdparty_source_url` required.
- all content: `thirdparty_refreshed` required, ISO `YYYY-MM-DD`.
- `summaries/`: `thirdparty_derived_from` required, paths relative to provider root.
- `thirdparty_title` optional; H1 default.
- Provenance is frontmatter; retrieval tier discrimination is path-based, not chunk metadata.

## Workflow

1. **Provider directory.** Lowercase dashed. Existing: `xai-api`, `claude-api`, `openai-api`, `google-api`, `lighter`, `mcp`, `coinbase-advanced`. New provider ⇒ add `PROVIDER_VENUES` entry in `scripts/rag/ingest-thirdparty-mirror.py` + scope block in `~/.gateway/rag.yaml`.
2. **Mirror upstream.** Services do not make outbound calls. Run host scrapers/curl, or use `web_fetch` for individual pages. Write one URL per `upstream/{slug}.md` with frontmatter. If blocked, use `curl_cffi` Chrome impersonation pattern from `research-article-ingest`.
3. **Summaries optional.** For large surfaces, write `summaries/` grouping workflows/endpoints; include `thirdparty_derived_from`.
4. **Validate/register.**

```bash
~/.venvs/universal/bin/python scripts/rag/ingest-thirdparty-mirror.py --provider {provider} --dry-run
~/.venvs/universal/bin/python scripts/rag/ingest-thirdparty-mirror.py --provider {provider}
```

Script skips `README.md`/`refresh.md`, classifies tier from path, validates frontmatter, computes SHA-256 `content_hash`, and POSTs `/article` with `venue`, `scope`, `published_date=thirdparty_refreshed`, `content_hash`, subdirectory, filename. Use `--strict` in CI; use `--force-index` only if watcher paused or chunking changed.

5. **Verify retrieval.** Query provider scope with tier prefixes:

```python
rag(op="search", arguments='{"scope":"xai_api","query":"web search tool citations","source_prefixes":["docs/thirdparty/xai-api/upstream/"]}')
rag(op="search", arguments='{"scope":"xai_api","query":"...","source_prefixes":["docs/thirdparty/xai-api/upstream/","docs/thirdparty/xai-api/summaries/"]}')
```

## Refresh cadence

Refresh annually or when vendor announces breaking change. Bump `thirdparty_refreshed` and rerun ingest; article `published_date` updates without chunk reindex unless forced.

## Do not

- put outbound HTTP in services;
- stamp tier metadata on chunks — path + `source_prefixes` is the discriminator;
- enable `vocab_mode: frontier` on thirdparty scopes; keep `none`;
- commit `docs/thirdparty/` content or mirror policy (gitignored);
- RAG-index raw `product/` HTML;
- invent article fields beyond title/venue/published_date/content_hash/scope/subdirectory/filename; put derived-from data in summary frontmatter.

## New provider checklist

1. Create `docs/thirdparty/{provider-slug}/{upstream,summaries,product}/`, `README.md`, `refresh.md`.
2. Add provider display venue + RAG scope to `PROVIDER_VENUES`.
3. Add `~/.gateway/rag.yaml` scope block mirroring `xai_api` (`prefixes`, `description`, `vocab_mode: none`).
4. Restart RAG.
5. Run ingest script and verify `rag` search.
