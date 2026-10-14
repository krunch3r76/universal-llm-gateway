Orchestrate a durable continuity root or formal mission lane.

**Shared Impresario entry.** `/orchestrate` and `/liaison` are the same tab entry for the
**Impresario** (the reasoning agent in the IDE tab). The Impresario infers **Light**, **Attended**, or
**Overnight** internally — the operator does not pick a gear or a separate birth mode ahead of time.

| Gear | Trigger (inferred) | Behavior |
|---|---|---|
| **Light** | No root slug and no durable objective or `todo:` | In-seat answer only. ¬ root birth, ¬ `liaison-arm-loop.sh`. |
| **Attended** | Root slug and/or durable objective or `todo:` | If `role:root` is missing: `Use orchestration-lanes` birth (create root, continuity doc, birth CHECKPOINT, stamp `role:root`), then attended seat (`ide-continuity`: register, arm loop). If `role:root` already exists: seat only. Lock `reason=held` by another `ide:` holder ⇒ worker tab (see `/liaison`). |
| **Overnight** | Departure utterance (`go under`, running overnight, leaving) | `ide-continuity` § go-under only (gear-3 metronome). ¬ a new attended seat loop. |

Bare `/orchestrate` or `/liaison` with no objective stays **Light** — do not mint an empty house.
The `checkpoint` verb remains `checkpoint-discipline` lean fire on an existing root — **not** birth and
not a substitute for Attended gear inference.

This command wraps skills. Use `orchestration-lanes` for session conversion, root/mission birth (the
**Attended lowering** when the house is missing), and clean-context resume. Use `mission-operator` to run a
formal mission and keep its turn schema conformant. Use `checkpoint-discipline` for root CHECKPOINT
maintenance.

For session-to-root conversion, follow the existing birth gate: mint the continuity charter surface, post
the birth CHECKPOINT with objective and concrete `Next-pickup`, then stamp `role:root`. Session close still
records provenance; the root carries continuity.

**Example (Attended birth + seat):** `/orchestrate music-lexicon-accord — unify lexicon schema and land tests`
→ Impresario infers Attended, runs `orchestration-lanes` birth if `role:root` absent, then `/liaison`
seat steps on that root.
