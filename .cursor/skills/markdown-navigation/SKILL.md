---
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
| `md_list` | `path` | List headings with path, level, line, chars. Always start here on unfamiliar docs. |
| `md_read` | `path`, `section` | Read one section body. Empty `section` reads preamble. |
| `md_replace` | `path`, `section`, `content` | Replace section body; heading line preserved. |
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

Section paths are hierarchical slash-separated heading paths: `Configuration/Database/Connection Pool`.

Resolution order:
1. exact full path;
2. suffix match for single-segment queries if unambiguous;
3. bare heading text if unambiguous.

Ambiguity returns full paths; copy the exact `path` from `md_list`. Literal `/` in a heading is escaped as `\/`. Preamble = `section:""`.

## Heading-less-content contract

For `md_replace`, `md_append`, `md_insert`: `content = body_only`.

- Matching ATX heading at content start (same level + text) is stripped and returns `normalized_heading:true`.
- Non-matching headings are literal Markdown and can create sibling/child sections. To add a real new section, use `md_insert` with explicit `heading`, `level`, `position`.

## Standard workflow

```text
md_list(path) → inspect chars/tree → md_read(path, section) → md_replace/md_append/md_insert/md_delete if needed
```

Use `chars` from `md_list` to decide whether to read a section directly or descend into children.

## Error recovery

| Error | Fix |
|---|---|
| `Section not found` | Run `md_list`; copy exact `path`. |
| `Ambiguous section` | Use full hierarchical path from error. |
| traversal rejected | Remove `../`; use sandbox-relative paths. |
| file not found | Check sandbox + path with `fs(op="list")`. |
| bad sandbox | Use `cortex` or `workspaces`; old aliases `files`, `project`, `context` are invalid. |

## Quick examples

```text
fs(op="md_list", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md")
fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="fs")
fs(op="md_replace", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Background", content="Updated text.\n")
fs(op="md_insert", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", heading="Risks", level=2, position="after", section="Background", content="- risk\n")
fs(op="md_delete", sandbox="workspaces", path="universal-llm-gateway/notes/research.md", section="Scratch")
fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="fs")
```
