# Card schema — load on birth / header write

Header table lives on the skill first screen. This file is the field shape. CHECKPOINT B1–B5 stay in `checkpoint-schema-profiles`. Birth mints the whole header in one write. Resume loads it before any `team_dispatch`. A heading whose body is an essay has left the motif.

## Seven headings (continuity-card v2)

Birth header, before any heading:

`<!-- continuity-card v=2 thread={N} reader_job=<one noun phrase> -->`

Ids first, prose second. A later reader follows whichever block is louder, so the ids sit above the story. No `## Entities` heading: entity ids are dated and live on the tip `## Entity refs` block (address + field name, never a value). No `## Sidecars` on the card: sidecar URIs are dated and live on the tip.

| Heading | Required | Owns |
|---|---|---|
| `## Skills` | yes | slug rows. Use each before the first move. A fallback path only when the slug is off this seat's loader |
| `## Runbooks` | yes | `runbook:house-close` plus the house-specific `runbook:*` |
| `## Rules` | yes | assertion-id / runbook-id rows that **override** global skill omit-paths. `_None yet._` legal; missing heading is not |
| `## Stance` | yes (`orchestrator_continuity`; `tick_charter` skips speech) | Use `ulg-for-llms` |
| `## Why this house` | yes | one paragraph **or** sidecar pointer. No dated status |
| `## Objective` | yes | Mission + In / Out (short). No dated status |
| `## House` | yes | what kind of house, where the tape lives, what this root does not own |

**Bounded archive:** sit tape / older `# Current` cuts / Windows accrual live on a **sidecar named on the tip**. ¬ grow the card. ¬ mint a second dialect file (`{N}-window-ledger` / `{N}-recall.md`). A v1 card (no `v=2` comment) stays legal and is not migrated by this schema.

### `## Rules` (field)

```
## Rules
∀ continuity-card:
  lane_law as assertion-id / runbook-id rows
  ≺ global skill omit-path
absence ⇒ house_gap
```

| | |
|---|---|
| **Holds** | Dispatch knobs · hop remainder · seat register · pointer to house-runbook law |
| **Shape** | Table `Rule \| id`, or short MUST lines that are ids. No recipes. No leftover execute holds. No book numbers. |
| **Birth** | Same write as Stance / Why / Objective / Runbooks. Empty table + `_None yet._` is legal; **missing heading is not**. |
| **Resume** | Fill-map slot 5b — then load the named runbook bodies. |
| **Tip** | CHECKPOINT indexes a pointer when a rule **changes**; speech stays on the card or the runbook. |
| **Specimen** | 9638 (liaison) · 9582 house runbook (trading) |

Mission text SOT = card `## Objective`. Hub mirrors same write. Speech order: `operator-posture` Rule 3 · `decision:continuity-resume-mission-open`. Drift: hub `content_hash` ≠ card sha.

## Body shapes (specimen-gated)

Reader-job shape lives on a **sidecar** (or is the house runbook). The card only points.

| Reader job | Where the body lives | Default edges | Specimen |
|---|---|---|---|
| sit-journal | sidecar `# Current` newest-first | `related_to` worker todos · `references` house runbook | 9582 · `runbook:9582-house` |
| liaison-inbox | sidecar **Recall** = newest-first `# Current` **full cut** (keys `score · stop · land · live · break · next`), written on `status` / `follow up` / every harvest, never edited. Inbox `owner=` table stays the **drop list**, not the recall surface. Inbound: `liaison-inbound.md`. **Temporary** drop list, ¬ default foreign-work dump. | `references` decisions/frictions · ≥1 runbook | 9638 · `runbook:liaison-seat-on-a-lane` |
| runbook-recall | the associated `runbook:*` body + hazards | `references` `runbook:*` | 9732 |

A row exists only with ≥1 living specimen. `screenshot-spec` is a sidecar kind, ¬ a catch-up shape.
