<!-- target:* -->
# Docs Write Guard

## Invariant

**∀ agent actions: ¬write to `docs/` unless the active workflow explicitly requires it.**

## What Gets Committed

Most repos gitignore everything under `docs/` except a canonical architecture
subtree **and** a small allowlist of public-promoted reference docs. Those
allowlisted paths are the only places under `docs/` outside the canonical
architecture subtree where agent writes can leak into a commit.

The rest of `docs/` is personal/internal reference (research corpus, engram,
vision docs) — gitignored, possibly RAG-indexed, but never committed. Agents
must not create new files there either; it adds workspace clutter for no
benefit.

### Public-promoted reference docs (committed, README-linked)

These files are whitelisted because a public README links to them — they are
public artifacts, not internal reference. Edits to these are permitted under
**manual user instruction** or when keeping them in sync with a README link.
New additions to this allowlist require a matching gitignore whitelist entry
**and** a row in the project's docs-write-guard table — do not silently
promote other `docs/` files to public.

## Allowed Workflows

### Public-promoted reference docs — regeneration workflow

Generated marker regions inside a public reference doc are off-limits to
manual string-replace edits; staleness inside markers ⇒ run the owning
regeneration command, never hand-edit.

### Subsystem architecture docs — overhaul-only

| Workflow | Scope |
|---|---|
| Architecture-doc generation step of an overhaul workflow | Generate + review architecture doc for the overhaul target directory |
| Doc-generation pipeline (via API) | Pipeline output — must pass review before replacing existing doc |
| Doc-opportunity proposal workflow | Propose new architecture docs (user approval required) |
| Manual user instruction | User explicitly says "write this to docs/..." |

¬ ad-hoc doc-check edits to these. Staleness noted in commit body only.

### Appendices (factual-correction subtree) — factual corrections

| Workflow | Scope |
|---|---|
| Doc-check pass (standalone or via commit workflow) | Targeted edits for stale paths, config keys, signals |
| Manual user instruction | User explicitly says "write this to docs/..." |

### Design docs — user-directed only

| Workflow | Scope |
|---|---|
| Manual user instruction | User explicitly directs edits |

∀ other contexts: **do not write to `docs/`.**

## Where Content Goes Instead

| Content type | Correct location |
|---|---|
| Session investigation notes | ephemeral scratch dir |
| Consultation/review output | ephemeral scratch or tasks dir |
| Response letters / integration guides | ephemeral scratch dir |
| Journal entries | project journal dir |
| Fix descriptions | ephemeral scratch or commit message |
| Implementation notes for a handler/module | in-source README or ephemeral scratch |
| Pipeline architecture comparisons | prompts/scratch dir |

## Canonical Architecture Subtree Quality Bar

This directory is the **canonical AI-readable architecture reference** — often
RAG-indexed and consumed by consultation models.

- Every factual claim must trace to source code
- **Honest guarantee:** generated content reflects what source *declares*
  (docstrings/signatures/imports), verified for doc<->docstring consistency — NOT
  docstring<->behavior truth. Generated blocks carry an inline provenance
  disclaimer + inventory/generated stamp injected deterministically by the
  doc-generate enforcement step.
- Generated sections wrapped in explicit `GENERATED:START`/`GENERATED:END`
  markers
- AUTHORED regions are preserved by a deterministic string-equality guard, never
  by an LLM; a dropped AUTHORED region fails the run
- No raw doc-generate output without review
- No inventories of non-architecture subsystems (e.g. pipeline handler dirs are
  implementation details, not architecture)

## Self-Check

Before writing any file under `docs/`:

1. **Is a qualifying workflow active?** → if not, stop
2. **Is the content durable and curated?** → if ephemeral, route to scratch
3. **Is it the canonical architecture subtree?** → source-traceable, reviewed,
   not raw output
4. **Is it a public-promoted reference doc?** → only the allowlisted paths,
   only under manual user instruction or README-sync; never silently promote
   another `docs/` file
<!-- /target:* -->
