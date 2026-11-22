---
description: Section-level navigation and editing of large Markdown-like documents via fs md_read/md_replace/md_append on cortex and workspaces; local libs/markdown_sections when cheaper.
trigger_match_terms: ["markdown-navigation", "markdown_navigation", "pdf", "docx", "chars", "md_list", "tooling-observability", "markdown", "file", "inspect", "section", "tree"]
---

# Markdown Navigation Tools

Secondary reference. Canonical MCP boot surface: `docs/tool-reference.md`. Use this skill for section-level navigation/editing of large Markdown-like documents via `fs` or local section helpers when hops favor them.

## Core rule

`doc.size > ~5k chars ⇒ md_list → md_read(section)`. Use whole-file `read` only for small docs. Threshold is character-based, not line-based.

`large_doc_edit ⇒ md_* server_side_edit ∧ ¬whole_file_rewrite` unless the edit genuinely spans the whole file.

## Primary interface: `fs` `md_*` (cortex and workspaces)

Markdown section ops are available on **both** `sandbox="cortex"` and `sandbox="workspaces"` (see `cursor-plugins/ulg-ecosystem/skills/fs/SKILL.md`). Md writes are lease-gated (`code` for workspaces, `{life, code}` for cortex).

| Op | Required args | Use |
|---|---|---|
| `md_list` | `path`, `offset?` | Manifest descriptors (`id`, `parent_id`, `line`, `char_count`, `children`). Page with `offset` cursor. |
| `md_read` | `path`, `section` | Read one section by `id` from `md_list`. Empty `section` returns manifest (not body). |
| `md_replace` | `path`, `section`, `target`, `content` | Patch substring within section body (`target` required). |
| `md_rewrite_section` | `path`, `section`, `content` | Replace entire section body (explicit rewrite). |
| `md_append` | `path`, `section`, `content` | Append to section body. |
| `md_insert` | `path`, `heading`, `level`, `position` | Create a new section at `end`/`after`/`before`; `after`/`before` require anchor `section`. |
| `md_delete` | `path`, `section` | Remove heading + body. |

`fs` is canonical for durable edits. Use `dispatch(tool="markdown")` only for `to_dict` / `from_dict`.

## Navigation (transport-neutral)

For **read-only** section tree / body inspection, pick the cheaper path — not MCP by default:

| Transport | When |
|---|---|
| **Local** | On-host: `libs/markdown_sections` (`list_sections`, `read_section`, `parse_sections`) against a file you already hold or a verified worktree path |
| **MCP `fs`** | Cross-resident paths, Share URI semantics, or when local file access is missing |

Do not claim unconditional token savings for one transport; measure hops and payload size for the job.

## Section read scope (parent sections)

For a **parent** section that has child headings, MCP `fs(op=md_read)` and local `libs/markdown_sections.read_section` do **not** return the same span. Pick the scope your job needs; do not assume parity.

| Route | Default parent read | Children |
|---|---|---|
| **MCP `fs` `md_read`** | Section **own_body** only (text before the first child heading) | Listed in manifest metadata (`children` / tree); **not** inlined in the read payload |
| **Local `read_section(text, section_path)`** | Full section **subtree** (`text[sec.start:sec.end]`, through the next same-or-higher-level heading) | **Included** in the returned string |

MCP responses disclose own-body reads via `span: "own_body"`, `includes_children: false`, and `subtree_char_count` for the full subtree size. Write ops use `span` (`own_body` default; `subtree` for legacy include-children behavior on writes only).

When you need **exact MCP read semantics** on a local file in the assigned worktree (same slice as `fs` sparse reads, including own_body on parents), call the sparse resolver — do not hand-roll a substitute:

- `services/mcp-server/tools/_sparse_document_read.py:resolve_document_read` (parameters: path, sandbox, text, read_sha256, section, …), or the managed `fs` / `markdown` read path that already routes through it.

## Sandboxes and paths

| Sandbox | Root | Example |
|---|---|---|
| `cortex` | `/data/files/` | `notes/research.md` — `md_list` / section `md_read` supported |
| `workspaces` | `/mnt/torus/projects/` | `universal-llm-gateway/docs/tool-reference.md` |

`workspaces` paths MUST include repo prefix: `universal-llm-gateway/...`.

PDF, DOCX, ODT, EML, HTML support `md_list` / `md_read` as converted read-only documents. Write ops reject converted formats.

## Section addressing

`md_list` / default `md_read` manifests expose wire **`id`** values (not a separate `path` key on manifest rows). ATX ids are hierarchical: `Configuration/Database/Connection Pool`. Line-anchored XML blocks use tag ids such as `<scope>`. Preamble is only `__preamble__` (no `[Preamble]` alias). Literal `/` in headings is escaped as `\/` in ids; duplicate headings append `#n`.

Pass the exact manifest `id` to `md_read` / write ops (`section=`).


Write ops accept optional `expected_sha256` (CAS on the full file pre-image) and `span` (`own_body` default on section writes; `subtree` for legacy include-children behavior). Use full hierarchical ids (`Parent/Child`) when the same heading text repeats under different parents. `md_create` creates a new markdown file from a JSON heading dict (create-only).

## Heading-less-content contract

For `md_replace`, `md_append`, `md_insert`: `content = body_only` (patch replacement for `md_replace`).

`md_replace` **requires `target`** — it patches within the section and cannot truncate unmentioned content. Full section rewrite ⇒ `md_rewrite_section` after `md_read`.

- Matching ATX heading at content start (same level + text) is stripped and returns `normalized_heading:true`.
- Non-matching headings are literal Markdown and can create sibling/child sections. To add a real new section, use `md_insert` with explicit `heading`, `level`, `position`.

## Standard workflow

```text
md_list(path) → inspect chars/tree → md_read(path, section) → md_replace/md_append/md_insert/md_delete if needed
```

Use `char_count` from `md_list` to decide whether to read a section directly or descend into children.

## Error recovery

| Error | Fix |
|---|---|
| `Section not found` | Run `md_list`; copy exact `id` (check `nearest_ids`). |
| `Ambiguous section` | Use the full manifest `id` from `md_list` (see `id_format_hint`). |
| traversal rejected | Remove `../`; use sandbox-relative paths. |
| file not found | Check sandbox + path with `fs(op="list")`. |
| bad sandbox | Use `cortex` or `workspaces`; old aliases `files`, `project`, `context` are invalid. |

## Quick examples

```text
fs(op="md_list", sandbox="cortex", path="notes/system/architecture/index.md")
fs(op="md_list", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md")
fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="fs")
fs(op="md_replace", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Background", target="old paragraph", content="Updated text.\n")
fs(op="md_rewrite_section", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Background", content="Fully new section body.\n")
fs(op="md_insert", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", heading="Risks", level=2, position="after", section="Background", content="- risk\n")
fs(op="md_delete", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Scratch")
```