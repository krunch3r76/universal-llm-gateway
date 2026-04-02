# MCP Server

> **Under active development.** Currently supports the Anthropic API (`mcp_servers` parameter). Web-based MCP and OpenAI API support are planned next.

An internet-facing [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes system capabilities as tools to cloud models. Runs as a containerized FastAPI application on `:443` with TLS (Let's Encrypt) and bearer token authentication.

## API Support Status

| Protocol | Status |
|----------|--------|
| Anthropic `mcp_servers` | **Shipped** — streamable HTTP transport |
| Web-based MCP clients | **Planned** |
| OpenAI tool protocol | **Planned** |

## Tool Categories

### Filesystem (`tools/filesystem.py`)

Sandboxed read/write/list in `/data/files`. Path traversal rejected at code level (defense-in-depth on top of the volume mount boundary).

| Tool | Purpose |
|------|---------|
| `write_file` | Write content to `.md`, `.txt`, `.csv`, `.docx`, `.pdf` |
| `read_file` | Read `.md`, `.txt`, `.csv`, `.docx`, `.odt` |
| `edit_file` | Atomic prepend/append/insert/replace on text files |
| `delete_file` | Delete individual files |
| `list_files` | Recursive file listing |

### Project (`tools/project.py`)

Access to the mounted project directory (`PROJECT_ROOT`). Listing and search
enumerate the real filesystem by default, so gitignored paths like `tmp/` and
`prompts/` are included. Set **`include_untracked=False`** to restrict results
to git-tracked files only. Direct `read_project_file` works for any on-disk path
under the mount. Writes require `project_access: rw` and
`PROJECT_READ_ONLY=false`.

| Tool | Purpose |
|------|---------|
| `list_project_files` | List files; optional `include_untracked` for gitignored trees |
| `read_project_file` | Read text file by relative path |
| `search_project_files` | Regex search; optional `include_untracked` |
| `fs(sandbox="project", ...)` | Preferred unified read/write/list/search surface over project-mounted files |

### Pipeline Consultation (`tools/pipeline_consult.py`)

RAG-grounded prompt-engineering advice for pipeline steps. Queries step metadata from the event service, auto-detects RAG scope from the model tier, and runs the `consult-prompt-engineer` pipeline via Stargate.

| Tool | Purpose |
|------|---------|
| `pipeline_consult` | Get expert prompt-engineering advice for a pipeline step issue |

### RAG (`tools/rag.py`)

Routes queries through Stargate's RAG pipelines via `host.docker.internal:9999`.

| Tool | Purpose |
|------|---------|
| `rag_search` | Semantic search via `rag-context` pipeline (multi-query rewriting, RRF merge, property boost) |
| `rag_answer` | Grounded Q&A via `rag-answer` or `rag-answer-deep` pipeline |
| `rag_list_scopes` | List available retrieval scopes |

### Web (`tools/web.py`)

Web search (Brave Search API) and URL fetching with SSRF protection.

| Tool | Purpose |
|------|---------|
| `web_search` | Brave Search API (requires `BRAVE_SEARCH_API_KEY`) |
| `web_fetch` | Fetch URL content; HTML extracted via trafilatura; SSRF guard blocks private IPs |

### Clips (`tools/clip.py`)

Access to browser-clipped content saved via the bookmarklet endpoint.

| Tool | Purpose |
|------|---------|
| `list_clips` | List saved clips with metadata |
| `read_clip` | Read clip content |

### Context (`tools/context.py`)

Structured access to the project's `tasks/` directory (journal, discoveries, lessons) and todos DB.

| Tool | Purpose |
|------|---------|
| `list_todos` | Query todos from `~/.cortex/todos.db` (status, domain, context, priority filters) |
| `list_journal_entries` | List recent journal entries with metadata |
| `read_journal_entry` | Read full journal entry by slug |
| `write_journal_entry` | Create journal entry with indexing |
| `list_context_directory` | Browse `tasks/` directory tree |
| `read_context_file` | Read any file from `tasks/` |
| `write_context_file` | Write file to `tasks/` |
| `edit_context_file` | Atomic prepend/append/insert/replace on `tasks/` files |
| `delete_context_file` | Delete a file from `tasks/` |

Configurable: read-only or read-write via `TASKS_READ_ONLY` / mount mode. Disabled entirely with `ENABLE_CONTEXT_TOOLS=false`.

### SQLite (`tools/sqlite.py`)

Safe read/write access to configured SQLite databases.

| Tool | Purpose |
|------|---------|
| `sql` | SELECT-only, parameterized, row-limited |
| `sqlite_execute` | INSERT/UPDATE/DELETE (DROP/PRAGMA blocked by default) |
| `sqlite_list_databases` | List configured database names |

### Browser (`tools/browser.py`)

Playwright Firefox with host cookie injection. **Disabled by default** — requires an explicit Compose override that relaxes the container's seccomp profile.

| Tool | Purpose |
|------|---------|
| `browser_navigate` | Navigate to URL |
| `browser_get_structure` | Page structural outline (landmarks, headings) |
| `browser_get_content` | Extract visible text (CSS selector scoping) |
| `browser_click` | Click element |
| `browser_fill` | Fill text input |
| `browser_screenshot` | Full-page screenshot (returned as image to vision models) |
| `browser_refresh_session` | Re-read Firefox cookies |
| `browser_load_cookies` | Inject cookies from JSON (Cookie-Editor export format) |

## Security Architecture

### Tiered Security Policies

Different tool categories carry different risk profiles and are isolated accordingly:

| Tier | Tools | Policy |
|------|-------|--------|
| **Default** | Filesystem, project, RAG, clips, context, SQLite | Docker default seccomp, non-root (`uid 1000`), 2GB memory limit, bearer token auth, TLS |
| **Browser** | `browser_*` tools | Adds 4 syscalls to seccomp (`clone`, `clone3`, `unshare`, `setns`) for Firefox's internal process sandbox. No capability escalation, no filesystem escape. Requires explicit Compose override |
| **Web** | `web_search`, `web_fetch` | Outbound HTTP allowed; SSRF guard blocks private/loopback addresses; fetch timeouts enforced |

### Container Isolation

- **Non-root**: Runs as `uid 1000:1000`
- **TLS**: Let's Encrypt certificates mounted read-only
- **Bearer auth**: All requests (except `/health`) require `Authorization: Bearer <token>`
- **Volume boundaries**: `/data/files` (rw), `/data/project` (ro), `/data/tasks` (configurable ro/rw)
- **Memory limit**: 2GB
- **TCP keepalive**: Prevents NAT eviction during long model-thinking pauses

### Browser Override Security

The browser Compose override (`mcp-server-browser.override.yml`) adds a custom seccomp profile:

- **Allows**: `clone`, `clone3`, `unshare`, `setns` — needed for Firefox's internal content process isolation
- **Does NOT allow**: mounting filesystems, gaining capabilities, escaping the container
- **Reversible**: Restart without the override to remove browser tools entirely

## Configuration

### Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MCP_AUTH_TOKEN` | Yes | — | Bearer token for authentication |
| `MCP_PROJECT_DIR` | Yes | — | Host path to project root (mounted read-only) |
| `MCP_DATA_DIR` | No | `~/mcp-data` | Host path for files, SQLite databases |
| `ENABLE_BROWSER_TOOLS` | No | `false` | Enable Playwright browser tools |
| `ENABLE_CONTEXT_TOOLS` | No | `true` | Enable tasks/ context tools |
| `TASKS_READ_ONLY` | No | `true` | App-level write guard for context tools |
| `MCP_TASKS_MOUNT_MODE` | No | `ro` | Docker mount mode for tasks volume |
| `BRAVE_SEARCH_API_KEY` | No | — | Brave Search API key for `web_search` |
| `STARGATE_URL` | No | `http://host.docker.internal:9999` | Stargate URL for RAG pipeline calls |

### Deployment

```bash
# Base (no browser tools)
docker compose -f docker/compose/mcp-server.yml up -d

# With browser automation
docker compose -f docker/compose/mcp-server.yml \
               -f docker/compose/mcp-server-browser.override.yml up -d
```

## Events

Event stream: `/tmp/mcp-events/current.jsonl`

Covers request lifecycle, tool invocations, browser sessions, SSE stream lifecycle, clip submissions.

## Key Files

| File | Responsibility |
|------|---------------|
| `server.py` | FastMCP app, bearer auth middleware, TLS config, clip endpoint |
| `mcp_events.py` | Event emission helpers |
| `tools/filesystem.py` | Sandboxed file read/write/edit/delete |
| `tools/project.py` | Project files: list/search default to on-disk files; set `include_untracked=False` for git-tracked-only results |
| `tools/pipeline_consult.py` | RAG-grounded prompt consultation |
| `tools/rag.py` | RAG pipeline integration via Stargate |
| `tools/web.py` | Brave Search + URL fetch with SSRF guard |
| `tools/clip.py` | Bookmarklet clip access |
| `tools/context.py` | Tasks directory structured access |
| `tools/sqlite.py` | SQLite database tools |
| `tools/browser.py` | Playwright Firefox automation |
| `tools/file_editor.py` | Atomic file edit operations |
