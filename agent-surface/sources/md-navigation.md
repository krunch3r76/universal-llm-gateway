<!-- target:* -->
# Markdown Navigation

## Invariant

**Invariant**: ∀ `.md`/`.mdc` reads via `fs` MCP: prefer section ops over
full-file reads. Full read is correct only when whole-file coverage is the
actual need (e.g. verifying a corpus for drift, reading a short file in its
entirety). Never dump a large reference file (rules, docs, tool-reference,
operational context) when a single section answers the question.

## Ops

| Op | Purpose |
|---|---|
| `md_list` | Heading tree (TOC) |
| `md_read(section=<heading>)` | One section by heading |
| `md_replace` / `md_append` / `md_delete` | Section-level edits |

## Pattern

```
# 1. Get the TOC
fs(sandbox="workspaces", op="md_list",
   path="universal-llm-gateway/.cursor/rules/some-rule_ws.mdc")

# 2. Read only the section you need
fs(sandbox="workspaces", op="md_read",
   path="universal-llm-gateway/.cursor/rules/some-rule_ws.mdc",
   section="Invariant")
```

Applies to: rule files (`.cursor/rules/`), docs (`docs/`), operational
context (`notes/system/shared/`), tool reference (`docs/tool-reference.md`),
agent guides, handoff packets, spec files.
<!-- /target:* -->
