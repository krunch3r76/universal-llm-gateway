---
trigger_match_terms: ["call", "write", "ops", "tooling-observability", "list", "search", "markdown", "section", "binary", "fs"]
description: On any fs(...) call — cortex/workspaces read-write; optional thread= roots workspaces in a lane worktree; md_* are workspaces-only.
---

# Skill: MCP fs Tool

**Trigger:** On any `fs(...)` call: read, write, list, search, binary ops. Markdown section ops (`md_*`) are **workspaces-only** — not on `sandbox="cortex"`.

## Sandboxes and paths

| Sandbox | Root | Path rule |
|---|---|---|
| `cortex` | `/data/files` | Path relative to sandbox root. |
| `workspaces` | `/mnt/torus/projects/` (default) | Repo-scoped paths include repo prefix: `universal-llm-gateway/services/...`; shared-parent paths do not: `.cursor/rules/...`. |
| `context` | `tasks/` | Path relative to tasks root. |

**Surface defaults:** `/mcp/code` — bare paths require `sandbox=` or a Share URI (`cortex://` / `workspaces://`). `/mcp/life` — omitted sandbox on an unqualified relative path defaults to cortex; prefer `cortex://notes/...`; workspaces refused unless the surface grant allows them.

Workspaces examples:

```text
fs(workspaces, path=".cursor/rules/handoff-dispatchers.mdc")       # shared-parent, no repo prefix
fs(workspaces, path="universal-llm-gateway/services/mcp-server/tools/cortex.py")
```

`path="projects/.cursor/..." ⇒ wrong`; root already is `/mnt/torus/projects/`.

## Lane worktree via `thread=` (workspaces)

Optional `thread=<agent-bus thread id>` with `sandbox="workspaces"` roots the call in that lane's **current branch worktree** (most-recent association), not the shared checkout.

| Rule | Behavior |
|---|---|
| Association present + worktree resolvable | Ops use that worktree; success may include `lane_thread` / `lane_branch` / `lane_worktree_root` |
| No association / unresolvable | **Refuse** (fail-closed) — never silent fallback to shared `master` |
| `thread` omitted | Prior behavior — shared workspaces root for the surface |

Branch → directory is resolved via `git worktree list --porcelain` matched on branch name (basename only). Do **not** invent a path from the branch string.

Lane-B admit auto-posts the association (`branch_associate`); life/code callers pass `thread=` explicitly until a seat wires it automatically.

**Life write grant is independent and still fail-closed** when `LIFE_PROJECT_ROOT` is unset — `thread=` alone does not unlock life workspaces writes. Unlock is a separate mission (`cortex://notes/system/prompts/life-write-unlock-via-lane-boot.md`). Plumbing: commit `bfc7fe34`.

## Search

`fs(op="search")` = **literal** (case-sensitive regex) content search over native text and converted PDF/DOCX/ODT/EML/HTML — **not** semantic retrieval.

- Params: `path` (file or dir), `content` (regex pattern — **not** `pattern=`). Workspaces directory mode accepts `include_untracked`.
- `/mcp/life`: cortex literal search **is available** via `fs(op="search", …)`; `find` and `search mode=filename` are `/mcp/code` only.
- Bug-class token sweeps (e.g. raw `--cdp-url` in handoffs): `fs(op="search", path="ephemeral/handoffs/", content="--cdp-url")` — no speculative probe needed.
- PDF search: sidecar-first (`*-readable.md`, `*.readable.md`, `*.extracted.md`), then layout-free pymupdf plaintext with timeout.
- Binary suffixes (images, archives, `.db`, `.safetensors`, compiled artifacts) skip; converted document formats search.
- Directory bounds: 20s aggregate converted extraction + max 10 converted-file extractions; overflow increments `skipped_converted`. Overall 20s wall budget covers enumeration + scan; native files >2MiB increment `skipped_oversized`. Search walks prune `tmp/` and `.runtime/` — narrow `path=` for large trees.

Response shape:

```json
{"path":"...","mode":"file|directory","matches":[{"file":"rel","line":42,"text":"..."}],"truncated":false,"skipped_converted":0,"skipped_oversized":0,"extraction_method":"sidecar_markdown|pymupdf_plaintext|converted|native_text"}
```

File mode omits match `file`. Invalid regex returns `Invalid regex pattern: ...`.

## Read and markdown ops

`read` converts supported document formats on both sandboxes. **`md_*` section ops
are workspaces-only** — not permitted on `sandbox="cortex"`.

Markdown section writes are text-file-only; converted formats reject.

Handoff implement packets use six line-anchored XML blocks (`<scope>`, `<invariants>`, `<task_guidance>`, `<corpus>`, `<mcp_capabilities>`, `<output_format>`). On those files, `md_list` surfaces each block as a navigable section and `md_read(section="<task_guidance>")` (or bare `task_guidance`) returns the block body without the tags. ATX heading navigation is unchanged on normal markdown.

`md_replace | md_append | md_insert ⇒ content = section body only`, not the heading. If body opens with an ATX heading matching target level/text, server strips it and returns `normalized_heading:true`.

`md_insert` creates a new section: `heading`, `level ∈ 1..6`, `position ∈ {end, after, before}`. `after/before` require `section` anchor; `after` lands past the anchor's whole subtree. Use `md_insert`, not append/replace, to add a section.

`multi_section_revision(existing_doc) ⇒ md_replace(each_section) ∧ ¬whole_file_reemit`; whole-file re-emission risks transcription drift. `guarded_whole_file_write ⇒ expected_sha256`.

Exception: when a downstream consumer needs the post-edit file hash (for example, a bus pointer), whole-file `write` returns `written_sha256`; `md_*` currently do not.

Successful `read` responses include `read_sha256`: bare lowercase hex of the on-disk source file bytes, computed before decode/conversion and independent of `offset`/`limit` windowing on `content`. Callers compose `sha256:` / `spec_sha256:` when citing on assertions — the field has no prefix. Symmetric to `written_sha256` on write ops.

## Recent commits (life catch-up)

`fs(op="recent_commits", sandbox="workspaces", path="universal-llm-gateway", since="<sha>")` is the life catch-up query for parallel git work — oneline subjects, no diffs, not a project index. Default last 15 (cap 20). `git_*` stays banned on life; do not commission Auto for `git log`.

Full guide: `markdown-navigation`; canonical tool reference: `universal-llm-gateway/docs/tool-reference.md`.

## Alternatives

- Semantic retrieval ⇒ `rag(op="search")`, not `fs` search.
- Large markdown ⇒ `md_list` / section `md_read` before whole-file search/read when possible.
- Filename lookup in workspaces ⇒ use `find` when available; `search` scans contents.

## Oversized responses (section tree before `rs_`)

When an `fs` (or other) result exceeds the response-size threshold **and** the payload is markdown-shaped, the size guard may return a **structure manifest** first — not only an opaque `rs_xxxx` stub:

- `kind: "markdown_structure"` with `sections[]` (`heading` / `level` / `path` / `line` / `chars` — md_list-equivalent; may be capped → `structure_truncated`).
- `selective_options` typically suggest `fs(md_list|md_read|read…)` against the durable `uri`/`path` when present.
- `full_retrieve_last_resort`: `retrieve(id="rs_…")` — use only after section navigation fails or you truly need the whole blob.

Anti-pattern: immediately `retrieve(id=rs_…)` and dump the full body when the section tree already tells you which heading to `md_read`. Source: `services/mcp-server/response_overflow_manifest.py`.
