---
description: Section-level navigation and editing of large Markdown-like documents via fs md_read/md_replace/md_append.
trigger_match_terms: ["markdown-navigation", "markdown_navigation", "pdf", "docx", "chars", "md_list", "tooling-observability", "markdown", "file", "inspect", "section", "tree"]
---

# Markdown Navigation Tools

Secondary reference. Canonical MCP boot surface: `docs/tool-reference.md`. Use this skill for section-level navigation/editing of large Markdown-like documents via `fs`.

## Core rule

`doc.size > ~5k chars ⇒ md_list → md_read(section)`. Use whole-file `read` only for small docs. Threshold is character-based, not line-based.

`large_doc_edit ⇒ md_* server_side_edit ∧ ¬whole_file_rewrite` unless the edit genuinely spans the whole file.

## Primary interface: `fs` `md_*` (workspaces only)

Markdown section ops are **not** available on `sandbox="cortex"`. Use `fs(op="read")`
for cortex files.

| Op | Required args | Use |
|---|---|---|
| `md_list` | `path`, `offset?` | Manifest descriptors (`id`, `parent_id`, `line`, `char_count`, `children`). Page with `offset` cursor. |
| `md_read` | `path`, `section` | Read one section by `id` from `md_list`. Empty `section` returns manifest (not body). |
| `md_replace` | `path`, `section`, `target`, `content` | Patch substring within section body (`target` required). |
| `md_rewrite_section` | `path`, `section`, `content` | Replace entire section body (explicit rewrite). |
| `md_append` | `path`, `section`, `content` | Append to section body. |
| `md_insert` | `path`, `heading`, `level`, `position` | Create a new section at `end`/`after`/`before`; `after`/`before` require anchor `section`. |
| `md_delete` | `path`, `section` | Remove heading + body. |

`fs` is canonical. Use `dispatch(tool="markdown")` only for `to_dict` / `from_dict`.

## Sandboxes and paths

| Sandbox | Root | Example |
|---|---|---|
| `cortex` | `/data/files/` | `notes/research.md` — use `read`, not `md_*` |
| `workspaces` | `/mnt/torus/projects/` | `universal-llm-gateway/docs/tool-reference.md` |

`workspaces` paths MUST include repo prefix: `universal-llm-gateway/...`.

PDF, DOCX, ODT, EML, HTML support `md_list` / `md_read` as converted read-only documents. Write ops reject converted formats.

## Section addressing

`md_list` / default `md_read` manifests expose wire **`id`** values (not a separate `path` key on manifest rows). ATX ids are hierarchical: `Configuration/Database/Connection Pool`. Line-anchored XML blocks use tag ids such as `<scope>`. Preamble is only `__preamble__` (no `[Preamble]` alias). Literal `/` in headings is escaped as `\/` in ids; duplicate headings append `#n`.

Pass the exact manifest `id` to `md_read` / write ops (`section=`).

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
fs(op="md_list", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md")
fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="fs")
fs(op="md_replace", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Background", target="old paragraph", content="Updated text.\n")
fs(op="md_rewrite_section", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Background", content="Fully new section body.\n")
fs(op="md_insert", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", heading="Risks", level=2, position="after", section="Background", content="- risk\n")
fs(op="md_delete", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Scratch")
fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="fs")
```
