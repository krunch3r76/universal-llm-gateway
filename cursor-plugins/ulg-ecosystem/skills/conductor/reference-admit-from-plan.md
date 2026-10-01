## Admit from an existing plan (substitute sketch)

**Trigger:** a written plan already exists (Plan-mode doc, consult note, operator
paste) and the operator says *densify + implement* / *admit the conductor*.
Select and follow this map; do not rebuild the path from source reading and 422s
(specimen `todo:cdp-ask-harvest-prompt-anchor` · a:36905).

### Hop → wire → product (the wire is `contract`; `packet_kind` is retired)

| Hop | Wire | Product | Written by |
|---|---|---|---|
| **Sketch** | `job=sketch` + `source_ref=todo:` (materializer reads todo `problem` / `scope`) | **shape bind** — R1 four blocks (`scope_pin` · `negative_space` · `output_envelope` · `transfer_predicate`) at `cortex://notes/system/consults/{slug}-sketch.md` | dispatched Sketch, or a **substitute** (below) |
| **Mission Composer** | `runbook:score-composer-author` — `job=freeform` + `prompt=` (¬ a materializer contract) | **conductor score** = the **densified todo**: dense spec at `source_uri`, implement-lane attrs, `stop_after` / `conductor_profile` pins, S5 attach when architecture is closed. ¬ a hand-written six-block file | dispatched Composer worker |
| **Conductor** | `job=conductor` + `source_ref=todo:` · `packet_path` **refused** (`conductor_with_packet_path`) · `prompt` refused | Stargate **materializes** the packet and **births the scoreboard** from the todo at admit | substrate |

`job=freeform` + `source_ref` is refused (`none_with_source_ref`) — it is not a
lighter conductor. Sweep `19ab1566a` rewrote `light-bounded` → `none` and dropped
`packet_kind=conductor` mechanically; any recipe still showing that pair is stale.

### What stands in for the Sketch

A plan is a valid Sketch substitute when it carries the four R1 blocks (prepend
them as a header when missing) and is registered as a sidecar the todo points at
(`scope:` / description / `density_triage_evidence_uri`). Frontmatter:
`consult_kind: sketch` · `substitute_for:` · `as_of:` HEAD sha · `status:` naming
that its decisions are **not independently checked** — the Mission Composer read
is that check. A substitute sketch closes the **Sketch hop**; it does **not**
close **G1**.

### Entry gate (`conductor_materialize.resolve_entry_gate`)

| Todo state at admit | Entry gate |
|---|---|
| scoreboard already born (re-admit / hop) | fold `entry_gate` = first non-DONE row — **wins** over every row below |
| attribute `derived_from` set | **G2** — packet says `G1 CLOSED by derived_from:…` |
| `density_triage=mechanical` | **G5** |
| else | **G1** |

### Skipping G1 legitimately — stamp **both**, or neither

| Stamp | Reader | Missing ⇒ |
|---|---|---|
| todo **attribute** `derived_from=document:{slug}-architecture-consult` | materializer → entry gate G2 | entry gate G1; conductor re-derives architecture |
| **structural edge** `todo --derived_from--> document:*` whose document carries `consult_kind=architecture` | witness fold (`_witness_g1`) → G1 renders DONE | G1 has no witness — fold leaves it OPEN while the seat is already driving G2 |

A `consult_kind: sketch` document never witnesses G1. Rival shapes still open ⇒
stamp neither; let G1 fire. Attach recipe: `work-item-seed-path` § S3 / S5.

### Todo seed contract (what the materializer and validators read)

| Key | Rule | 422 |
|---|---|---|
| `files_expected` · `acceptance_criteria` · `required_skills` | non-empty `list[str]`, no blank items. Optional at seed; owed before `implement_ready` | `implement_attr_shape_invalid` — payload carries `canonical_keys` + `expected_shape` |
| `files_modified` · `acceptance` | aliases — refused | `implement_attr_alias_rejected` — payload names `canonical` |
| `required_skills[i]` | slug ∈ `config/skills.yaml`; mirror each with a `requires` edge todo → `agent_skill:` | `required_skills_uncatalogued` |
| `implement_ready` | **never seeded** — an assertion the conductor stamps at Gate-2 (`implement_ready_assertion_id` mirrors it) | — |
| `problem` · `scope` · `source_uri` | prose the Sketch materializer and spec hash read | — |
| `density_triage` · `kind` · `spawned_by_friction=<int>` · `stop_after` · `conductor_profile` · `summon_mode` · `derived_from` | attrs folded into the packet header | — |

### `dispatch_thread_id` shapes

§ First-utterance spawn. Shape 1 keeps a continuity root that is not an
operator lane. When that root is an operator lane, the admit 422s
`conductor_summoning_operator_lane`. Shape 2: pre-create a pending-empty child
(`bus_lifecycle_state=pending`, `turn_count=0`, `parent_thread=<lane>`,
`cse_registration_id` null) and pass that child as `dispatch_thread_id` and
`reuse_thread`. The 422 `conductor_coord_split_refused` repeats the
three legal shapes in `details.hint` — read the payload before probing threads.

### Preflight gate (dispatch-kernel "agent_bus + stargate + GIW up")

`manage(action="status")` answers up/down compactly. `busy_status` is the
lane-**holder** read (§ Admit post-admit check); it is large and does not scope
by `service=` — do not spend it on up/down (a:36905 item 5).

